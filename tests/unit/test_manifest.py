"""Unit tests for manifest schema and serialization."""

import json

from a11yfix.manifest import (
    SCHEMA_VERSION,
    AppliedFix,
    FileFormat,
    Finding,
    Manifest,
    Severity,
    ValidationResult,
    validate_manifest_dict,
)


def test_empty_manifest_serialization():
    m = Manifest(file_path="/tmp/x.docx", file_format=FileFormat.DOCX)
    d = m.to_json()
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["file_format"] == "docx"
    assert d["stage_1_findings_total"] == 0
    assert d["residual_findings"] == []


def test_round_trip_with_finding():
    f = Finding(
        id="x-1",
        rule_id="alt-text-missing",
        severity=Severity.ERROR,
        wcag_sc=["1.1.1"],
        officecli_path="/slide[1]/picture[@id=1]",
    )
    m = Manifest(
        file_path="/tmp/x.pptx",
        file_format=FileFormat.PPTX,
        stage_1_findings_total=1,
        residual_findings=[f],
        validation=ValidationResult(status="ok"),
    )
    raw = json.dumps(m.to_json())
    parsed = json.loads(raw)
    assert validate_manifest_dict(parsed) == []


def test_validate_rejects_unknown_format():
    bad = {
        "schema_version": SCHEMA_VERSION,
        "file_path": "/x",
        "file_format": "xlsx",
        "stage_1_findings_total": 0,
        "residual_findings": [],
    }
    errs = validate_manifest_dict(bad)
    assert any("file_format" in e for e in errs)


def test_applied_fix_json():
    af = AppliedFix(
        finding_id="f-1",
        rule_id="alt-text-missing",
        officecli_path="/slide[1]/picture[@id=1]",
        stage=2,
        before="",
        after="{'alt': 'hi'}",
    )
    j = af.to_json()
    assert j["stage"] == 2
