"""Unit tests for table_headers rule."""

from a11yfix.ooxml.docx_reader import open_docx
from a11yfix.rules.table_headers import TableHeaderRule


def test_table_without_header_detected(docx_no_title):
    doc = open_docx(docx_no_title)
    findings = list(TableHeaderRule().detect(doc))
    assert any(f.rule_id == "table-header-missing" for f in findings)
