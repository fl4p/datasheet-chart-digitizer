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


def test_split_coer_symbol_skips_row_index_column(tmp_path):
    # IMBG75R007M2H.pdf.nop.csv row 134: the symbol renders as "C o(er)" and the
    # leading cell is the CSV row index. The value is 490, never the index 134.
    root = _write_csv(tmp_path, "SPLIT", [
        "134,energy related 9),C o(er),-,,490,-,,pF VDS = 0...500 VGS,,,",
    ])
    assert parse_output_charge_reference("SPLIT", root).coer_pf == 490.0


def test_split_cotr_symbol_slices_original_text(tmp_path):
    # Pins the spaced Co(tr) symbol through the generalised whitespace-tolerant
    # match (the old Co(tr)-only fallback also passed this row).
    root = _write_csv(tmp_path, "SPLITTR", [
        "77,time related,C o (tr),-,,1250,-,,pF VDS = 0...400 V,,,",
    ])
    assert parse_output_charge_reference("SPLITTR", root).cotr_pf == 1250.0


def test_coer_on_row_index_with_unsplit_symbol_unchanged(tmp_path):
    root = _write_csv(tmp_path, "PLAIN", [
        '143,"Effective output capacitance, energy related 9)",Co(er),-,178,-,pF,"VGS = 0 V, VDS = 0...500 V"',
    ])
    assert parse_output_charge_reference("PLAIN", root).coer_pf == 178.0


def test_footnote_after_split_symbol_is_not_the_value(tmp_path):
    # STP42N65M5 row 97 (codex review 2026-11-24): HEAD read 96 (row index),
    # the first fix read 2 (the footnote); the PDF states 100 pF.
    root = _write_csv(tmp_path, "STFN", [
        "96,C o(er) (2),Equivalent capacitance energy related,,-,100,-,pF,,,",
    ])
    assert parse_output_charge_reference("STFN", root).coer_pf == 100.0


def test_exact_symbol_beats_earlier_spaced_mention(tmp_path):
    # A spaced mention in a description cell must not move the value search.
    root = _write_csv(tmp_path, "EXACT", [
        '12,"see C o(er) note 9",Co(er),-,490,-,pF,"VGS = 0 V, VDS = 0...500 V"',
    ])
    assert parse_output_charge_reference("EXACT", root).coer_pf == 490.0


def test_parenthesised_multi_digit_value_is_not_a_footnote(tmp_path):
    # Only single-digit "(n)" markers are footnotes; "(45)" stays a value.
    root = _write_csv(tmp_path, "PAREN", [
        "5,Co(er),(45),60,pF,,",
    ])
    assert parse_output_charge_reference("PAREN", root).coer_pf == 45.0


# Test-condition numbers are not the value (codex review pass 2, 2026-11-24).
# Stripping condition text from the joined row was tried and backed out: it
# returned the "1" of "f = 1 MHz" for 95 NXP/Toshiba parts. The value search
# now skips a number only when it is a condition's own value.


def test_condition_voltage_is_not_qoss(tmp_path):
    # AOB66811L row 138: HEAD read VDS=40V as Qoss; PDF 112 nC.
    root = _write_csv(tmp_path, "AOSQ", [
        '138,Qoss,Output Charge,"VGS=0V,VDS=40V",,112,,nC,,,',
    ])
    assert parse_output_charge_reference("AOSQ", root).qoss_pc == 112000.0


def test_inline_conditions_are_not_qoss(tmp_path):
    # PSMN011-30YLC row 121: HEAD read VDS = 15 V; stripping read f = 1; PDF 3.8 nC.
    root = _write_csv(tmp_path, "NXPQ", [
        "121,Qoss,output charge VGS = 0 V; VDS = 15 V; f = 1 MHz; - 3.8 - nC,,,",
    ])
    assert parse_output_charge_reference("NXPQ", root).qoss_pc == 3800.0


def test_condition_range_and_garbled_decimal_are_not_cotr(tmp_path):
    # STB32N65M5 row 409 (PDF 210 pF) and an IXYS garbled render (PDF 2700 pF).
    root = _write_csv(tmp_path, "STTR", [
        '409,Co(tr)(1),Equivalent capacitance time related,"VGS = 0 V,VDS = 0 to 520 V",-,210,-,pF,',
    ])
    assert parse_output_charge_reference("STTR", root).cotr_pf == 210.0
    root = _write_csv(tmp_path, "IXTR", [
        "656,C o(tr) Ti mer e l a t edVDS =0 . 8*VDSS,,,,2700,pF,,,",
    ])
    assert parse_output_charge_reference("IXTR", root).cotr_pf == 2700.0


def test_placeholder_dash_and_value_labels_are_not_conditions(tmp_path):
    # Codex pass-2 constructed rows: "-" is the empty min column, not a range;
    # TYP=/VALUE= label the value itself.
    for row, want in (
        ("1,Qoss,Output charge,VDS=40,-,112,150,nC", 112000.0),
        ("1,Qoss,Output charge,TYP=45,MAX=60,nC", 45000.0),
        ("1,Qoss,Output charge,VALUE=45,60,nC", 45000.0),
    ):
        root = _write_csv(tmp_path, "LBL", [row])
        assert parse_output_charge_reference("LBL", root).qoss_pc == want, row


def test_at_frequency_condition_is_not_qoss(tmp_path):
    # NCES075P013D7 row 60: HEAD read the 1 of "@1MHz" as Qoss; PDF 340 nC.
    root = _write_csv(tmp_path, "NCEQ", [
        "60,Output charge,Qoss,Calculated by Coss(f)VDS@1MHz,,340,,nC,,",
    ])
    assert parse_output_charge_reference("NCEQ", root).qoss_pc == 340000.0


def test_footnotes_figures_bounds_and_typ_column(tmp_path):
    # Codex review pass 3 (2026-11-24), corpus rows checked against the PDFs.
    for part, row, field, want in (
        # NXP GAN3R2-100CBEAZ: "[2]" after the conditions is a footnote (PDF 50 nC)
        ("G1", "29,Qoss,output charge,VGS = 0 V; VDS = 50 V; Tj = 25 °C,[2],-,50,-,nC", "qoss_pc", 50000.0),
        # NXP GANE3R9-150QBAZ: "Fig. 15" is a figure reference (PDF 130 nC)
        ("G2", "29,Qoss,output charge,VGS = 0 V; VDS = 75 V; Fig. 15,[1],-,130,-,nC", "qoss_pc", 130000.0),
        # NXP GAN3R2-100CBEAZ Co(er): "VDS <= 50 V" is a bound (PDF 700 pF)
        ("G3", "132,Co(er) effective output,0 V <= VDS <= 50 V; VGS = 0 V,,[1],-,700,-,pF", "coer_pf", 700.0),
        # Infineon BSC016N06NS: min/typ/max, typ served (PDF typ 81 nC; HEAD read min 60)
        ("B1", "119,Qoss,60,81,102,nC,\"VDD=30 V,VGS=0 V\"", "qoss_pc", 81000.0),
        # Infineon SPP20N60CFD garbled font: HGS/HDS are VGS/VDS (PDF 83 pF; HEAD read 480)
        ("S1", '106,"Effective output capacitance,2) energy related",Co(er),"HGS=0V, HDS=0V to 480V",-,83,-,pF', "coer_pf", 83.0),
    ):
        root = _write_csv(tmp_path, part, [row])
        assert getattr(parse_output_charge_reference(part, root), field) == want, row


def test_value_words_are_not_condition_names(tmp_path):
    # Codex pass 3 constructed rows: Typical/Total/Ratio label the value.
    for row in (
        "1,Qoss,Typical=45,MAX=60,nC",
        "1,Qoss,Total=45,60,nC",
        "1,Qoss,Ratio=45,60,nC",
        "1,Qoss,Total=45,nC",
    ):
        root = _write_csv(tmp_path, "WORD", [row])
        assert parse_output_charge_reference("WORD", root).qoss_pc == 45000.0, row
