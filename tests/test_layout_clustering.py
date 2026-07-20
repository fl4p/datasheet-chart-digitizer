from __future__ import annotations

from pathlib import Path

import fitz

from datasheet_chart_digitizer.layout_clustering import (
    PageLayoutSignature,
    cluster_signatures,
    discover_pdfs,
    scan_pdf,
)


def _signature(
    identity: str,
    vendor: str,
    tokens: tuple[str, ...],
    *,
    role: str = "chart",
    mode: str = "native",
) -> PageLayoutSignature:
    return PageLayoutSignature(
        id=identity,
        pdf=identity.split("#", 1)[0],
        vendor=vendor,
        page=1,
        page_count=1,
        role=role,
        text_mode=mode,
        page_size="a4-p",
        width_pt=595.0,
        height_pt=842.0,
        tokens=tokens,
        fingerprint="-".join(tokens),
        metrics={},
    )


def test_discovery_excludes_generated_pdf_copies(tmp_path: Path) -> None:
    vendor = tmp_path / "vishay"
    vendor.mkdir()
    canonical = vendor / "part.pdf"
    canonical.touch()
    for suffix in ("r600", "gs", "cups", "sips"):
        (vendor / f"part.pdf.{suffix}.pdf").touch()
    nested = vendor / "part.pdf.r600_ocrmypdf.pdf.gs.pdf.r600.pdf"
    nested.touch()
    (vendor / "ordinary-name.pdf").touch()
    (vendor / "_samples").mkdir()
    (vendor / "_samples" / "sample.pdf").touch()

    paths, variants = discover_pdfs(tmp_path)

    assert paths == [vendor / "ordinary-name.pdf", canonical]
    assert {item["transform"] for item in variants} == {"r600", "gs", "cups", "sips"}
    assert all(item["canonical_path"] == str(canonical) for item in variants)
    assert all(item["canonical_exists"] for item in variants)
    nested_info = next(item for item in variants if item["path"] == str(nested))
    assert nested_info["transform_chain"] == ["r600_ocrmypdf", "gs", "r600"]


def test_structurally_equal_pages_cluster_across_vendors() -> None:
    shared = ("role:chart", "mode:native", "size:a4-p", "frame:0:1:2-3", "caption:transfer:1:0")
    pages = [
        _signature("nxp/a.pdf#p1", "nxp", shared),
        _signature("vishay/b.pdf#p1", "vishay", shared + ("word-count:32+",)),
        _signature("rohm/c.pdf#p1", "rohm", ("role:chart", "mode:native", "size:a4-p", "image:2:4:large:1")),
    ]

    clusters, assignments = cluster_signatures(pages, threshold=0.70)

    assert assignments[pages[0].id] == assignments[pages[1].id]
    assert assignments[pages[0].id] != assignments[pages[2].id]
    shared_cluster = next(item for item in clusters if item["cluster_id"] == assignments[pages[0].id])
    assert shared_cluster["vendor_count"] == 2


def test_role_and_text_mode_are_hard_cluster_boundaries() -> None:
    tokens = ("frame:0:1:1",)
    pages = [
        _signature("a.pdf#p1", "a", tokens),
        _signature("b.pdf#p1", "b", tokens, role="table"),
        _signature("c.pdf#p1", "c", tokens, mode="vector_outline"),
    ]

    _, assignments = cluster_signatures(pages, threshold=0.0)

    assert len(set(assignments.values())) == 3


def test_scanner_finds_chart_page_structure(tmp_path: Path) -> None:
    vendor = tmp_path / "ixys"
    vendor.mkdir()
    pdf = vendor / "part.pdf"
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    for left in (55, 310):
        page.draw_rect(fitz.Rect(left, 130, left + 220, 330), color=(0, 0, 0))
        for index in range(1, 5):
            x = left + index * 44
            page.draw_line((x, 130), (x, 330), color=(0.5, 0.5, 0.5))
    page.insert_text((55, 350), "Figure 3. Transfer Characteristics", fontsize=10)
    page.insert_text((310, 350), "Figure 4. Capacitance Characteristics", fontsize=10)
    document.save(pdf)
    document.close()

    [signature] = scan_pdf(pdf, tmp_path)

    assert signature.role == "chart"
    assert signature.text_mode == "native"
    assert signature.metrics["closed_frames"] == 2
    assert "caption:transfer:2:0" in signature.tokens
    assert "caption:capacitances:2:2" in signature.tokens
