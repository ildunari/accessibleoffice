"""Unit tests for table_headers rule."""

from a11yfix.ooxml.docx_reader import open_docx
from a11yfix.ooxml.pptx_reader import open_pptx
from a11yfix.rules.table_headers import TableHeaderRule


def test_table_without_header_detected(docx_no_title):
    doc = open_docx(docx_no_title)
    findings = list(TableHeaderRule().detect(doc))
    assert any(f.rule_id == "table-header-missing" for f in findings)


def test_one_cell_layout_table_not_header_candidate(tmp_path):
    from docx import Document  # type: ignore[import-untyped]

    path = tmp_path / "layout_table.docx"
    docx = Document()
    table = docx.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "layout"
    docx.save(path)

    findings = list(TableHeaderRule().detect(open_docx(path)))

    assert findings == []


def test_plain_ppt_table_missing_header_is_not_auto_fixed(tmp_path):
    from pptx import Presentation  # type: ignore[import-untyped]
    from pptx.util import Inches  # type: ignore[import-untyped]

    path = tmp_path / "plain_table.pptx"
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[6])
    shape = slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1))
    shape.table.cell(0, 0).text = "plain A"
    shape.table.cell(0, 1).text = "plain B"
    shape._element.graphic.graphicData.tbl.tblPr.set("firstRow", "0")
    pres.save(path)

    doc = open_pptx(path)
    findings = list(TableHeaderRule().detect(doc))

    assert len(findings) == 1
    assert findings[0].extra["visually_header"] is False
    assert TableHeaderRule().fix_deterministic(findings[0], doc) is None


def test_visually_header_ppt_table_is_auto_fixed(tmp_path):
    from pptx import Presentation  # type: ignore[import-untyped]
    from pptx.util import Inches  # type: ignore[import-untyped]

    path = tmp_path / "header_table.pptx"
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[6])
    shape = slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1))
    shape.table.cell(0, 0).text = "Header A"
    shape.table.cell(0, 1).text = "Header B"
    shape.table.cell(0, 0).text_frame.paragraphs[0].runs[0].font.bold = True
    shape._element.graphic.graphicData.tbl.tblPr.set("firstRow", "0")
    pres.save(path)

    doc = open_pptx(path)
    finding = next(iter(TableHeaderRule().detect(doc)))
    ops = TableHeaderRule().fix_deterministic(finding, doc)

    assert finding.extra["visually_header"] is True
    assert ops is not None
    assert ops[0].props == {"firstRow": "1"}
