"""Regression tests for Word/DOCX detection precision."""

from __future__ import annotations

from docx import Document  # type: ignore[import-untyped]
from docx.oxml import parse_xml  # type: ignore[import-untyped]
from PIL import Image  # type: ignore[import-untyped]

from a11yfix.ooxml.docx_reader import open_docx
from a11yfix.rules.alt_text import AltTextRule
from a11yfix.rules.heading_structure import HeadingStructureRule
from a11yfix.rules.list_semantics import ListSemanticsRule
from a11yfix.rules.table_headers import TableHeaderRule


def test_docx_fake_list_inside_table_cell_is_not_reported_with_body_path(tmp_path):
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].text = "• typed bullet in a layout cell"
    path = tmp_path / "nested_fake_list.docx"
    doc.save(path)

    findings = list(ListSemanticsRule().detect(open_docx(path)))

    assert findings == []


def test_docx_fake_heading_inside_table_cell_is_not_reported_with_body_path(tmp_path):
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    paragraph = table.cell(0, 0).paragraphs[0]
    run = paragraph.add_run("Visual heading in a table")
    run.bold = True
    path = tmp_path / "nested_fake_heading.docx"
    doc.save(path)

    findings = list(HeadingStructureRule().detect(open_docx(path)))

    assert findings == []


def test_docx_nested_table_does_not_emit_second_body_table_path(tmp_path):
    doc = Document()
    outer = doc.add_table(rows=2, cols=2)
    outer.cell(0, 0).add_table(rows=1, cols=1)
    path = tmp_path / "nested_table.docx"
    doc.save(path)

    findings = list(TableHeaderRule().detect(open_docx(path)))

    assert len(findings) == 1
    assert findings[0].officecli_path == "/body/tbl[1]/tr[1]"


def test_docx_decorative_docpr_image_is_not_missing_alt(tmp_path):
    img = tmp_path / "image.png"
    Image.new("RGB", (50, 50), color="blue").save(img)
    doc = Document()
    inline_shape = doc.add_picture(str(img))
    doc_pr = inline_shape._inline.docPr
    extlst = parse_xml(
        '<a:extLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:adec="http://schemas.microsoft.com/office/drawing/2017/decorative">'
        '<a:ext uri="{C183D7F6-B498-43B3-948B-1728B52AA6E4}">'
        '<adec:decorative val="1"/></a:ext></a:extLst>'
    )
    doc_pr.append(extlst)
    path = tmp_path / "decorative.docx"
    doc.save(path)

    findings = list(AltTextRule().detect(open_docx(path)))

    assert findings == []
