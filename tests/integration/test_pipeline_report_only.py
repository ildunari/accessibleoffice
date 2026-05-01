"""End-to-end report-only run on synthetic fixtures."""

import json
import subprocess
import sys
from pathlib import Path


def test_cli_report_only_on_pptx(pptx_no_alt: Path, tmp_path: Path) -> None:
    out = tmp_path / "manifest.json"
    cmd = [
        sys.executable,
        "-m",
        "a11yfix.cli",
        str(pptx_no_alt),
        "--report-only",
        "--output",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    assert data["file_format"] == "pptx"
    assert data["stage_1_findings_total"] >= 1
    rule_ids = {f["rule_id"] for f in data["residual_findings"]}
    assert "alt-text-missing" in rule_ids or "slide-title-missing" in rule_ids


def test_cli_report_only_on_docx(docx_no_title: Path, tmp_path: Path) -> None:
    out = tmp_path / "manifest.json"
    cmd = [
        sys.executable,
        "-m",
        "a11yfix.cli",
        str(docx_no_title),
        "--report-only",
        "--output",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    assert data["file_format"] == "docx"
    rule_ids = {f["rule_id"] for f in data["residual_findings"]}
    assert "document-title-missing" in rule_ids
    assert "table-header-missing" in rule_ids
