"""Rule: document core property `dc:title` is missing or empty.

WCAG 2.4.2 (Page Titled). Severity: Tip.

Both .docx and .pptx store core props under /docProps/core.xml.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import (
    BaseRule,
    DocumentHandle,
    OfficecliOp,
    RuleMeta,
    register_rule,
)


class DocumentTitleRule(BaseRule):
    meta = RuleMeta(
        rule_id="document-title-missing",
        severity=Severity.TIP,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=["2.4.2"],
        plain_impact="The document has no title — assistive tech announces only the filename.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        core = getattr(doc, "core_xml", None)
        title = ""
        if core is not None:
            t = core.find(qn("dc:title"))
            if t is not None and t.text:
                title = t.text.strip()
        if title:
            return
        yield Finding(
            id="doc-title-missing",
            rule_id=self.meta.rule_id,
            severity=self.meta.severity,
            wcag_sc=self.meta.wcag_sc,
            officecli_path=(
                "/document/coreProperties/title"
                if doc.file_format == FileFormat.DOCX
                else "/presentation/coreProperties/title"
            ),
            current_value="",
            plain_impact=self.meta.plain_impact,
            extra={"filename": Path(doc.path).stem},
        )

    def fix_deterministic(self, finding: Finding, doc: DocumentHandle) -> list[OfficecliOp] | None:
        # Use filename stem if it's reasonably descriptive (≥3 chars, not "untitled" etc.)
        stem = finding.extra.get("filename", "")
        if not isinstance(stem, str) or len(stem) < 3:
            return None
        if stem.lower() in {"untitled", "document", "presentation", "deck", "doc"}:
            return None
        return [
            OfficecliOp(
                verb="set",
                path=finding.officecli_path,
                props={"value": stem.replace("_", " ").replace("-", " ").strip()},
            )
        ]


register_rule(DocumentTitleRule())
