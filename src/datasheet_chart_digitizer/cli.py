from __future__ import annotations

import importlib
import os
import platform
import subprocess
import sys
from pathlib import Path


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
    print(f"  {'--open':<26} open the command's output (the --out path) in the")
    print(f"  {'':<26} system viewer when it finishes successfully")


def _extract_option(args: list[str], name: str) -> str | None:
    """Return the value of ``--name VALUE`` or ``--name=VALUE`` if present."""
    for index, arg in enumerate(args):
        if arg == name and index + 1 < len(args):
            return args[index + 1]
        prefix = name + "="
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def _resolve_open_target(command: str, rest: list[str]) -> Path | None:
    """Best-effort location of what a command wrote, for ``--open``."""
    explicit = _extract_option(rest, "--out")
    if explicit:
        return Path(explicit)
    if command == "find":
        # find_charts default output directory.
        return Path("out/datasheet_charts")
    if command in {"digitize-capacitance", "digitize-breakdown-voltage"}:
        # These default their output alongside the positional charts.json.
        positionals = [arg for arg in rest if not arg.startswith("-")]
        if positionals:
            return Path(positionals[0]).parent
    return None


def _open_path(path: Path) -> None:
    """Open a file or directory in the platform's default handler."""
    if not path.exists():
        print(f"dsdig: --open target does not exist: {path}", file=sys.stderr)
        return
    target = str(path)
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["open", target], check=False)
        elif system == "Windows":
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", target], check=False)
    except OSError as exc:  # opener missing / not on PATH
        print(f"dsdig: could not open {path}: {exc}", file=sys.stderr)


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

    module_name, returns_exit_code = _DISPATCH[command]
    module = importlib.import_module(f".{module_name}", __package__)
    sys.argv = [f"dsdig {command}", *rest]

    exit_code = 0
    try:
        result = module.main()
        if returns_exit_code and result is not None:
            exit_code = int(result)
    except SystemExit as exc:  # argparse errors, explicit exits, etc.
        code = exc.code
        exit_code = 0 if code is None else (code if isinstance(code, int) else 1)

    # Only reveal output for a clean run; a failed/aborted command has nothing
    # trustworthy to show.
    if open_after and exit_code == 0:
        target = _resolve_open_target(command, rest)
        if target is None:
            print(
                "dsdig: --open could not determine the output location "
                "(pass --out to use it)",
                file=sys.stderr,
            )
        else:
            _open_path(target)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
