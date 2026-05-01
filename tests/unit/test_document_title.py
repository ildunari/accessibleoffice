"""Unit tests for document_title rule."""

from a11yfix.ooxml.docx_reader import open_docx
from a11yfix.rules.document_title import DocumentTitleRule


def test_no_title_detected(docx_no_title):
    doc = open_docx(docx_no_title)
    findings = list(DocumentTitleRule().detect(doc))
    assert any(f.rule_id == "document-title-missing" for f in findings)


def test_title_present_not_flagged(docx_with_title):
    doc = open_docx(docx_with_title)
    findings = list(DocumentTitleRule().detect(doc))
    assert not findings
