"""Unit tests for table_headers rule."""

from a11yfix.ooxml.docx_reader import open_docx
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
