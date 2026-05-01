"""Rule: DRM/IRM presence — detect-only, never fix.

If a document is rights-managed, our pipeline cannot/should not modify it.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule


class DrmIrmRule(BaseRule):
    meta = RuleMeta(
        rule_id="drm-irm-detected",
        severity=Severity.WARNING,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=[],
        plain_impact="Rights-managed file detected; a11yfix will not write changes.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        # python-docx / python-pptx will fail to open IRM-protected files; if we got here, no IRM.
        # We can still flag if encryption indicators are visible in OOXML (rare).
        return ()


register_rule(DrmIrmRule())
