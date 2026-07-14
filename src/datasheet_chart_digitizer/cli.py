from __future__ import annotations

import sys


def main() -> None:
    commands = {
        "find": "find chart panels and emit charts.json",
        "digitize-capacitance": "digitize MOSFET Ciss/Coss/Crss charts",
        "digitize-vpl": "digitize MOSFET gate-charge curves and estimate Vpl",
        "digitize-reverse-recovery": "digitize diode Qrr/Irm/trr/S charts (25/125C, AO style)",
        "digitize-breakdown-voltage": "digitize V(BR)DSS vs Tj charts (Infineon Diagram 15 style)",
        "export-coss-spice": "export digitized Coss(V) as knots and a SPICE Qoss table",
        "export-coss-dslib": "export validation-gated dslib (V, Coss, Crss) knot triples",
    }
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print("usage: dsdig <command> [args...]")
        print()
        print("Find and digitize chart curves in datasheet PDFs.")
        print()
        print("commands:")
        for name, desc in commands.items():
            print(f"  {name:<22} {desc}")
        raise SystemExit(0 if len(sys.argv) >= 2 else 2)

    command, rest = sys.argv[1], sys.argv[2:]
    if command == "find":
        from . import find_charts

        sys.argv = ["dsdig find", *rest]
        find_charts.main()
        return
    if command == "digitize-capacitance":
        from . import mosfet_capacitance

        sys.argv = ["dsdig digitize-capacitance", *rest]
        mosfet_capacitance.main()
        return
    if command == "digitize-vpl":
        from . import gate_charge_vpl

        sys.argv = ["dsdig digitize-vpl", *rest]
        raise SystemExit(gate_charge_vpl.main())
        return
    if command == "digitize-reverse-recovery":
        from . import reverse_recovery

        sys.argv = ["dsdig digitize-reverse-recovery", *rest]
        reverse_recovery.main()
        return
    if command == "digitize-breakdown-voltage":
        from . import breakdown_voltage

        sys.argv = ["dsdig digitize-breakdown-voltage", *rest]
        breakdown_voltage.main()
        return
    if command == "export-coss-spice":
        from . import coss_export

        sys.argv = ["dsdig export-coss-spice", *rest]
        coss_export.main()
        return
    if command == "export-coss-dslib":
        from . import coss_dslib

        sys.argv = ["dsdig export-coss-dslib", *rest]
        coss_dslib.main()
        return
    raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
