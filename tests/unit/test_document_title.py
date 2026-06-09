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


def test_deterministic_op_targets_root_title_property(docx_no_title):
    """officecli stores the document title as the `title` property on the root
    node (`set / --prop title=...`). The op MUST target path "/" with a `title`
    prop — not the legacy `/document/coreProperties/title` path with a `value`
    prop, which officecli 1.0.x rejects as "Path not found"."""
    doc = open_docx(docx_no_title)
    rule = DocumentTitleRule()
    finding = next(f for f in rule.detect(doc) if f.rule_id == "document-title-missing")
    ops = rule.fix_deterministic(finding, doc)
    assert ops is not None and len(ops) == 1
    op = ops[0]
    assert op.verb == "set"
    assert op.path == "/"
    assert "title" in op.props
    assert "value" not in op.props
    assert op.props["title"]  # non-empty
