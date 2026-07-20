from __future__ import annotations

import contextlib
import importlib
import io
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import TextIO


# command -> (module, main-returns-an-exit-code)
_DISPATCH: dict[str, tuple[str, bool]] = {
    "find": ("find_charts", False),
    "digitize-capacitance": ("mosfet_capacitance", False),
    "digitize-vpl": ("gate_charge_vpl", True),
    "digitize-reverse-recovery": ("reverse_recovery", False),
    "digitize-breakdown-voltage": ("breakdown_voltage", False),
    "digitize-transfer": ("transfer_characteristics", False),
    "annotate": ("annotate_pdf", False),
    "export-coss-spice": ("coss_export", False),
    "export-coss-dslib": ("coss_dslib", False),
}

_COMMANDS = {
    "find": "find chart panels and emit charts.json",
    "digitize-capacitance": "digitize MOSFET Ciss/Coss/Crss charts",
    "digitize-vpl": "digitize MOSFET gate-charge curves and estimate Vpl",
    "digitize-reverse-recovery": "digitize diode Qrr/Irm/trr/S charts (25/125C, AO style)",
    "digitize-breakdown-voltage": "digitize V(BR)DSS vs Tj charts (Infineon Diagram 15 style)",
    "digitize-transfer": "digitize Id(Vgs,Tj) saturation transfer curves and fit temp-co",
    "annotate": "detect supported charts and write an annotated PDF copy",
    "export-coss-spice": "export digitized Coss(V) as knots and a SPICE Qoss table",
    "export-coss-dslib": "export validation-gated dslib (V, Coss, Crss) knot triples",
}

# Suffixes worth opening, in priority order: the annotated/overlay PDF first,
# then contact sheets and overlay rasters.
_OPENABLE_SUFFIXES = (".pdf", ".png", ".webp", ".svg")
_MAX_OPEN = 8


def _print_usage() -> None:
    print("usage: dsdig <command> [args...] [--open]")
    print()
    print("Find and digitize chart curves in datasheet PDFs.")
    print()
    print("commands:")
    for name, desc in _COMMANDS.items():
        print(f"  {name:<26} {desc}")
    print()
    print("global options:")
    print(f"  {'--open':<26} when the command finishes successfully, open what it")
    print(f"  {'':<26} produced (the annotated PDF / overlays, else the --out dir)")


def _extract_option(args: list[str], name: str) -> str | None:
    """Return the value of ``--name VALUE`` or ``--name=VALUE`` if present."""
    for index, arg in enumerate(args):
        if arg == name and index + 1 < len(args):
            return args[index + 1]
        prefix = name + "="
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def _resolve_out_dir(command: str, rest: list[str]) -> Path | None:
    """Best-effort output directory, used only as a fallback for ``--open``."""
    explicit = _extract_option(rest, "--out")
    if explicit:
        return Path(explicit)
    if command == "find":
        return Path("out/datasheet_charts")
    if command in {"digitize-capacitance", "digitize-breakdown-voltage"}:
        positionals = [arg for arg in rest if not arg.startswith("-")]
        if positionals:
            return Path(positionals[0]).parent
    return None


def _artifacts_from_output(printed: str) -> list[Path]:
    """Extract existing openable files (PDFs/overlays) the command reported.

    Commands print the paths they write (annotate prints the annotated PDF,
    digitize-vpl prints overlays + BATCH_CONTACT_SHEET). Parsing that is far
    more reliable than guessing each command's output layout.
    """
    found: list[Path] = []
    for line in printed.splitlines():
        # Paths are printed bare or after a label (``KEY: /path``); a colon is
        # never part of a POSIX path, so splitting on it and whitespace isolates
        # the token cleanly.
        for token in line.replace(":", " ").split():
            token = token.strip().strip(",'\"")
            if not token.lower().endswith(_OPENABLE_SUFFIXES):
                continue
            for candidate in (Path(token), Path.cwd() / token):
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved.is_file() and resolved not in found:
                    found.append(resolved)
    return found


def _select_artifacts(artifacts: list[Path]) -> list[Path]:
    """Pick the most relevant artifacts to open, PDFs first."""
    pdfs = [p for p in artifacts if p.suffix.lower() == ".pdf"]
    if pdfs:
        return pdfs[:_MAX_OPEN]
    images = [p for p in artifacts if p.suffix.lower() in {".png", ".webp", ".svg"}]
    preferred = [
        p for p in images if "overlay" in p.name.lower() or "contact" in p.name.lower()
    ]
    return (preferred or images)[:_MAX_OPEN]


def _open_paths(paths: list[Path]) -> None:
    """Open files/directories in the platform's default handler."""
    system = platform.system()
    for path in paths:
        if not path.exists():
            print(f"dsdig: --open target does not exist: {path}", file=sys.stderr)
            continue
        target = str(path)
        try:
            if system == "Darwin":
                subprocess.run(["open", target], check=False)
            elif system == "Windows":
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", target], check=False)
        except OSError as exc:  # opener missing / not on PATH
            print(f"dsdig: could not open {path}: {exc}", file=sys.stderr)


def _input_paths(rest: list[str]) -> set[Path]:
    """Existing files passed as *inputs* — never things to open.

    The ``--out``/``--work-dir`` values are outputs, so they are excluded even
    though they parse like ordinary file tokens; otherwise the produced file
    would be filtered out of the open set.
    """
    outputs: set[Path] = set()
    for option in ("--out", "--work-dir"):
        value = _extract_option(rest, option)
        if value:
            try:
                outputs.add(Path(value).resolve())
            except OSError:
                pass
    inputs: set[Path] = set()
    for arg in rest:
        if arg.startswith("-"):
            continue
        try:
            resolved = Path(arg).resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved not in outputs:
            inputs.add(resolved)
    return inputs


def _reveal_output(command: str, rest: list[str], printed: str, exit_code: int) -> None:
    """Open what a command produced (for ``--open``).

    Keyed on artifacts that were reported AND exist on disk, not on the exit
    code: commands like ``annotate`` exit non-zero when some panels error yet
    still write a valid annotated PDF. Only when nothing was produced does the
    exit code decide between opening the output directory (clean run) and
    staying silent (genuine failure).
    """
    inputs = _input_paths(rest)
    # Exclude inputs BEFORE ranking, so an input PDF cannot out-prioritize the
    # real (e.g. image) outputs a command produced.
    produced = [p for p in _artifacts_from_output(printed) if p not in inputs]
    artifacts = _select_artifacts(produced)
    if artifacts:
        _open_paths(artifacts)
        return
    if exit_code != 0:
        return  # failed with nothing to show
    out_dir = _resolve_out_dir(command, rest)
    if out_dir is not None and out_dir.exists():
        _open_paths([out_dir])
        return
    print(
        "dsdig: --open found nothing to open "
        "(no output files reported; pass --out to open the directory)",
        file=sys.stderr,
    )


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        _print_usage()
        raise SystemExit(0 if argv else 2)

    command, rest = argv[0], argv[1:]
    if command not in _DISPATCH:
        raise SystemExit(f"unknown command: {command}")

    # --open is a global flag consumed here; strip it before the subcommand's
    # own argument parser sees it (it would otherwise reject the unknown flag).
    open_after = "--open" in rest
    if open_after:
        rest = [arg for arg in rest if arg != "--open"]

    # The subcommand's own parser owns its --help and cannot know about the
    # dispatcher-level --open, so append it to whatever help the subcommand
    # prints.
    help_requested = any(arg in {"-h", "--help"} for arg in rest)

    module_name, returns_exit_code = _DISPATCH[command]
    module = importlib.import_module(f".{module_name}", __package__)
    sys.argv = [f"dsdig {command}", *rest]

    # When --open is set, tee the command's stdout so we can both stream it and
    # learn which files it wrote. Otherwise run untouched.
    tee = _Tee(sys.stdout) if open_after else None
    exit_code = 0
    try:
        with (contextlib.redirect_stdout(tee) if tee else contextlib.nullcontext()):
            result = module.main()
        if returns_exit_code and result is not None:
            exit_code = int(result)
    except SystemExit as exc:  # argparse errors, explicit exits, etc.
        code = exc.code
        exit_code = 0 if code is None else (code if isinstance(code, int) else 1)

    if help_requested and exit_code == 0:
        print()
        print("global options (accepted on every dsdig command):")
        print("  --open                     when the command finishes successfully, open")
        print("                             what it produced (annotated PDF / overlays)")

    if tee is not None:
        _reveal_output(command, rest, tee.getvalue(), exit_code)

    raise SystemExit(exit_code)


class _Tee(io.TextIOBase):
    """Write-through stream: echoes to the real stdout and records the text."""

    def __init__(self, target: TextIO) -> None:
        self._target = target
        self._buffer: list[str] = []

    def write(self, text: str) -> int:  # type: ignore[override]
        self._target.write(text)
        self._buffer.append(text)
        return len(text)

    def flush(self) -> None:  # type: ignore[override]
        self._target.flush()

    def getvalue(self) -> str:
        return "".join(self._buffer)


if __name__ == "__main__":
    main()
