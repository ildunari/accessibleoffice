"""Manifest: the stage-3 → stage-4 contract. Versioned. Frozen schema.

Defines Finding, AppliedFix, and Manifest dataclasses with JSON (de)serialization.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    TIP = "tip"
    INTELLIGENT = "intelligent_services"


class FileFormat(str, Enum):
    DOCX = "docx"
    PPTX = "pptx"


@dataclass
class Finding:
    """A single accessibility issue detected in a document."""

    id: str
    rule_id: str
    severity: Severity
    wcag_sc: list[str]
    officecli_path: str
    current_value: str = ""
    plain_impact: str = ""
    why_human_needed: str = ""
    related_findings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class AppliedFix:
    """Record of a fix that was applied (stage 2 or stage 3)."""

    finding_id: str
    rule_id: str
    officecli_path: str
    stage: int  # 2 or 3
    before: str = ""
    after: str = ""
    ai_model: str | None = None
    confidence: float | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    status: str  # "ok" | "errors" | "skipped"
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Manifest:
    """The full handoff manifest written after stages 1-3."""

    file_path: str
    file_format: FileFormat
    schema_version: str = SCHEMA_VERSION
    file_backup_path: str | None = None
    scan_timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    stage_1_findings_total: int = 0
    stage_2_fixes_applied: list[AppliedFix] = field(default_factory=list)
    stage_3_fixes_applied: list[AppliedFix] = field(default_factory=list)
    residual_findings: list[Finding] = field(default_factory=list)
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(status="skipped"))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "file_path": self.file_path,
            "file_format": self.file_format.value,
            "file_backup_path": self.file_backup_path,
            "scan_timestamp": self.scan_timestamp,
            "stage_1_findings_total": self.stage_1_findings_total,
            "stage_2_fixes_applied": [f.to_json() for f in self.stage_2_fixes_applied],
            "stage_3_fixes_applied": [f.to_json() for f in self.stage_3_fixes_applied],
            "residual_findings": [f.to_json() for f in self.residual_findings],
            "validation": asdict(self.validation),
        }

    def write(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=2))


def validate_manifest_dict(d: dict[str, Any]) -> list[str]:
    """Return a list of validation errors; empty list = valid."""
    errs: list[str] = []
    if d.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version mismatch: {d.get('schema_version')} != {SCHEMA_VERSION}")
    for k in ("file_path", "file_format", "stage_1_findings_total", "residual_findings"):
        if k not in d:
            errs.append(f"missing key: {k}")
    if d.get("file_format") not in {"docx", "pptx"}:
        errs.append(f"invalid file_format: {d.get('file_format')}")
    return errs
