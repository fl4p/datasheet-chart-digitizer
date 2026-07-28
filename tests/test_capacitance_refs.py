"""Anchor extraction across the Infineon table renders.

The export gate (coss_dslib) refuses without Coss+Crss anchors, so this module is a
guard: every repair pass below must be calibrated in BOTH directions — the real
datasheet shape it exists for parses to the correct values, and the ambiguous/
unsupported shape refuses instead of guessing.
"""

from pathlib import Path

import pymupdf

from datasheet_chart_digitizer.capacitance_refs import (
    parse_capacitance_anchors,
    parse_output_charge_reference,
)


def _write_csv(tmp_path: Path, part: str, rows: list[str]) -> Path:
    (tmp_path / f"{part}.pdf.nop.csv").write_text("\n".join(rows) + "\n")
    return tmp_path


def _write_pdf(tmp_path: Path, part: str, lines: list[str]) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line)
        y += 14
    doc.save(tmp_path / f"{part}.pdf")
    doc.close()


def test_legacy_complete_rows_unchanged(tmp_path):
    """The classic N5/NF2S render (value + pF + VDS per row) — the pre-extension
    behavior, byte-for-byte: typ (first number), row-local condition."""
    root = _write_csv(tmp_path, "LEGACY", [
        '50,Input capacitance,Ciss,-,2900 3770,pF,"VGS=0 V,VDS=40 V,f=1 MHz",,',
        '51,Output capacitance,Coss,-,490 637,pF,"VGS=0 V,VDS=40 V,f=1 MHz",,',
        '52,Reverse transfer capacitance,Crss,-,23 40,pF,"VGS=0 V,VDS=40 V,f=1 MHz",,',
    ])
    a = parse_capacitance_anchors("LEGACY", root)
    assert {n: (x.value_pf, x.vds_v) for n, x in a.items()} == {
        "Ciss": (2900.0, 40.0), "Coss": (490.0, 40.0), "Crss": (23.0, 40.0)}


def test_block_inherits_unit_and_condition_from_first_row(tmp_path):
    """The M7/M8 render: pF and the VGS/VDS/f condition are printed once for the
    contiguous block (here on the Ciss row); Coss/Crss carry bare value pairs."""
    root = _write_csv(tmp_path, "M7", [
        '96,Input capacitance 7),Ciss,-,2800,3600,pF,"VGS=0 V,VDS=50 V,f=1 MHz",,',
        "97,Output capacitance 7),Coss,1170,1520,,,,,",
        "98,Reverse transfer capacitance 7),Crss,13,23,,,,,",
    ])
    a = parse_capacitance_anchors("M7", root)
    assert {n: (x.value_pf, x.vds_v) for n, x in a.items()} == {
        "Ciss": (2800.0, 50.0), "Coss": (1170.0, 50.0), "Crss": (13.0, 50.0)}


def test_block_with_conflicting_vds_refuses(tmp_path):
    """Known-bad: two distinct VDS values inside one block must refuse the
    conditionless rows, not pick one. Absence of an anchor is a rejected export;
    a wrong anchor voltage is a silently mis-anchored curve."""
    root = _write_csv(tmp_path, "AMBIG", [
        '96,Input capacitance,Ciss,-,2800,3600,pF,"VGS=0 V,VDS=50 V,f=1 MHz",,',
        '97,Output capacitance,Coss,1170,1520,,"VDS=25 V",,,',
        "98,Reverse transfer capacitance,Crss,13,23,,,,,",
    ])
    a = parse_capacitance_anchors("AMBIG", root)
    assert "Ciss" in a          # row-local, complete
    assert "Crss" not in a      # block VDS ambiguous -> refused
    assert a["Ciss"].vds_v == 50.0
    # Pre-existing pass-1 behavior, pinned so a change is visible: a pF-less row
    # with its OWN VDS anchors from the whole-tail number (cross-checked by the
    # export gate downstream).
    assert a["Coss"].value_pf == 1170.0 and a["Coss"].vds_v == 25.0


def test_block_with_negative_vds_refuses(tmp_path):
    """Known-bad: a p-channel block condition ('VDS=-50 V') must land in the
    distinct-value set and refuse — the unsigned regex used to make the negative
    invisible, so nothing guarded this input class."""
    root = _write_csv(tmp_path, "PCH", [
        '40,Input capacitance,Ciss,-,2800,3600,pF,"VGS=0 V,VDS=-50 V,f=1 MHz",,',
        "41,Output capacitance,Coss,1170,1520,,,,,",
    ])
    a = parse_capacitance_anchors("PCH", root)
    assert "Coss" not in a


def test_nf_row_does_not_inherit_block_pf(tmp_path):
    """Known-bad: a row carrying its own nF unit must not be anchored as pF via
    block inheritance (IXFB class: Ciss 18 nF next to a pF Coss row)."""
    root = _write_csv(tmp_path, "IXFB", [
        '31,Input capacitance,Ciss,,,,18,,nF',
        '32,Output capacitance,Coss,-,4300,,pF,"VDS=25 V",,',
        '33,Reverse transfer capacitance,Crss,120,180,,,,,',
    ])
    a = parse_capacitance_anchors("IXFB", root)
    assert "Ciss" not in a
    assert a["Coss"].value_pf == 4300.0
    assert a["Crss"].value_pf == 120.0 and a["Crss"].vds_v == 25.0


def test_min_typ_max_render_serves_typ_not_min(tmp_path):
    """The ao/nxp render lists three ascending numbers (min typ max); the repair
    pass must serve the MIDDLE one — first-number silently served the min. Four
    or more numbers is an unrecognized render and refuses."""
    root = _write_csv(tmp_path, "AO2", [
        '131,Ciss,Input Capacitance,600,890,1200,pF,"VDS=25 V,VGS=0 V,f=1 MHz",,',
        "132,Coss,Output Capacitance,25,42,60,pF,,,",
        "133,Crss,Reverse Capacitance,2,5,9,11,pF,,,",
    ])
    a = parse_capacitance_anchors("AO2", root)
    assert a["Coss"].value_pf == 42.0      # typ, not the min (25)
    assert a["Coss"].vds_v == 25.0
    assert "Crss" not in a                 # 4 numbers -> refused


def test_degraded_subscript_pdf_condition_matches(tmp_path):
    """The IRF100PW-class text layer drops subscript glyphs and wraps the
    condition ('V =0 V,' / '=50 V, =1 MHz'); the page-compacted signature match
    must still recover the single voltage."""
    root = _write_csv(tmp_path, "IRF", [
        "32,Ciss,-,12000,16000,pF,,,,",
        "33,Coss,-,1800,2300,pF,,,,",
        "34,Crss,-,80,140,pF,,,,",
    ])
    _write_pdf(tmp_path, "IRF", [
        "Input capacitance",
        "C",
        "pF",
        "V =0 V,",
        "=50 V,  =1 MHz",
        "V",
        "f",
    ])
    a = parse_capacitance_anchors("IRF", root)
    assert {n: (x.value_pf, x.vds_v) for n, x in a.items()} == {
        "Ciss": (12000.0, 50.0), "Coss": (1800.0, 50.0), "Crss": (80.0, 50.0)}


def test_pdf_fallback_refuses_negative_and_zero_voltages(tmp_path):
    """Known-bad: the p-channel signature pair (VDS=0 V and VDS=-15 V lines) must
    refuse — signed collection sees both; a lone zero/negative never anchors."""
    root = _write_csv(tmp_path, "PCHPDF", [
        "32,Ciss,-,1200,-,pF,,,,",
    ])
    _write_pdf(tmp_path, "PCHPDF", [
        "Input capacitance",
        "VDS=0 V, VGS=0 V, f=1 MHz",
        "VDS=-15 V, VGS=0 V, f=1 MHz",
    ])
    assert parse_capacitance_anchors("PCHPDF", root) == {}


def test_qoss_vint_fallback_from_degraded_pdf(tmp_path):
    """The IRF100PW-class Qoss row: the CSV keeps 'Qoss,-,213,320,nC' but loses
    the 'VDD=50 V, VGS=0 V' condition; the degraded PDF prints it subscript-less
    ('V =50 V,' / '=0 V'). The inverse token order (=xV before =0V), anchored on
    the Output charge row marker, recovers vint without confusing it with the
    capacitance signature."""
    root = _write_csv(tmp_path, "QIRF", [
        "48,Qoss,-,213,320,nC,,,,",
    ])
    _write_pdf(tmp_path, "QIRF", [
        "Output charge 9)",
        "Q",
        "-",
        "213",
        "320",
        "nC",
        "V =50 V,",
        "=0 V",
        "V",
    ])
    ref = parse_output_charge_reference("QIRF", root)
    assert ref.qoss_pc == 213000.0
    assert ref.vint_v == 50.0


def test_qoss_vint_fallback_refuses_two_distinct_voltages(tmp_path):
    """Known-bad: two different output-charge condition voltages in the PDF must
    leave vint None rather than pick one."""
    root = _write_csv(tmp_path, "QDUAL", [
        "48,Qoss,-,213,320,nC,,,,",
    ])
    _write_pdf(tmp_path, "QDUAL", [
        "Output charge",
        "213 nC VDD=50 V, VGS=0 V",
        "Output charge",
        "199 nC VDD=40 V, VGS=0 V",
    ])
    ref = parse_output_charge_reference("QDUAL", root)
    assert ref.qoss_pc == 213000.0
    assert ref.vint_v is None


def test_qoss_vint_csv_row_condition_still_wins(tmp_path):
    """A row-local condition keeps beating the PDF fallback (which must not even
    be needed): pre-existing behavior pinned."""
    root = _write_csv(tmp_path, "QCSV", [
        '48,Output charge,Qoss,-,213,320,nC,"VDD=50 V,VGS=0 V",,',
    ])
    ref = parse_output_charge_reference("QCSV", root)
    assert ref.qoss_pc == 213000.0
    assert ref.vint_v == 50.0


def test_broken_pdf_refuses_instead_of_raising(tmp_path):
    """Known-bad: an unopenable {part}.pdf must degrade to a missing anchor, not
    raise out of the digitization of an otherwise-good chart."""
    root = _write_csv(tmp_path, "BROKEN", [
        "32,Ciss,-,1200,-,pF,,,,",
    ])
    (tmp_path / "BROKEN.pdf").write_bytes(b"not a pdf at all")
    assert parse_capacitance_anchors("BROKEN", root) == {}


def test_block_without_unit_evidence_refuses(tmp_path):
    """Known-bad for the block-repair pass: a conditionless block that never
    mentions pF contributes nothing — bare numbers next to a capacitance symbol
    are not evidence of a pF value. (A row-local VDS without a unit is accepted
    by the pre-existing per-row pass and cross-checked by the export gate; this
    test pins the REPAIR pass, which must be stricter.)"""
    root = _write_csv(tmp_path, "NOUNIT", [
        "40,Something,Ciss,-,2800,3600,,,,",
        "41,Something,Coss,1170,1520,,,,,",
    ])
    assert parse_capacitance_anchors("NOUNIT", root) == {}


def test_separated_tables_do_not_lend_conditions(tmp_path):
    """A diagram legend or summary row far away from the block must not donate
    its unit/condition: rows more than 2 lines apart are separate blocks."""
    root = _write_csv(tmp_path, "FAR", [
        '10,Output capacitance,Coss,1170,1520,,,,,',
        "11,,,,,,,,,",
        "12,,,,,,,,,",
        "13,,,,,,,,,",
        '14,legend,Ciss,-,2800,3600,pF,"VGS=0 V,VDS=50 V,f=1 MHz",,',
    ])
    a = parse_capacitance_anchors("FAR", root)
    assert "Coss" not in a


def test_pdf_condition_fallback_single_value(tmp_path):
    """The NF2S-AKMA1 render: the CSV has value+pF rows but the condition column
    was lost entirely; the PDF's characteristics table states the capacitance
    signature (VGS=0, VDS, f=1 MHz) with one distinct voltage."""
    root = _write_csv(tmp_path, "NF2S", [
        "32,Ciss,-,12000,-,pF,,,,",
        "33,Coss,-,1900,-,pF,,,,",
        "34,Crss,-,83,-,pF,,,,",
    ])
    _write_pdf(tmp_path, "NF2S", [
        "Input capacitance",
        "VGS=0 V, VDS=40 V, f=1 MHz",
        "Output capacitance",
        "VGS=0 V, VDS=40 V, f=1 MHz",
    ])
    a = parse_capacitance_anchors("NF2S", root)
    assert {n: (x.value_pf, x.vds_v) for n, x in a.items()} == {
        "Ciss": (12000.0, 40.0), "Coss": (1900.0, 40.0), "Crss": (83.0, 40.0)}


def test_pdf_fallback_refuses_multiple_distinct_voltages(tmp_path):
    """Known-bad: two different capacitance-signature voltages in the PDF (e.g. a
    dual-die sheet) must refuse the fallback for the whole part."""
    root = _write_csv(tmp_path, "DUAL", [
        "32,Ciss,-,12000,-,pF,,,,",
        "33,Coss,-,1900,-,pF,,,,",
    ])
    _write_pdf(tmp_path, "DUAL", [
        "Input capacitance",
        "VGS=0 V, VDS=40 V, f=1 MHz",
        "Output capacitance",
        "VGS=0 V, VDS=25 V, f=1 MHz",
    ])
    assert parse_capacitance_anchors("DUAL", root) == {}


def test_pdf_fallback_ignores_non_capacitance_conditions(tmp_path):
    """Gate-charge style conditions (no VGS=0/f=1MHz trio) must not feed the
    fallback voltage even when they carry a VDS."""
    root = _write_csv(tmp_path, "GC", [
        "32,Ciss,-,12000,-,pF,,,,",
    ])
    _write_pdf(tmp_path, "GC", [
        "Gate charge total",
        "VDD=40 V, ID=50 A, VGS=0...10 V",
        "Input capacitance",
    ])
    assert parse_capacitance_anchors("GC", root) == {}
