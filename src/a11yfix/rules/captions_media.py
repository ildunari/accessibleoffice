"""Rule: media (audio/video) without captions.

WCAG 1.2.1 / 1.2.2. Severity: Warning. Detect-only.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule


class CaptionsMediaRule(BaseRule):
    meta = RuleMeta(
        rule_id="captions-media-missing",
        severity=Severity.WARNING,
        formats={FileFormat.PPTX},
        wcag_sc=["1.2.1", "1.2.2"],
        plain_impact="Audio/video content without captions excludes deaf and hard-of-hearing users.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            # Look for p:videoFile or p:audioFile under timing or shape relationships
            has_media = False
            for el in slide_xml.iter():
                if el.tag.endswith("}videoFile") or el.tag.endswith("}audioFile"):
                    has_media = True
                    break
            if not has_media:
                continue
            yield Finding(
                id=f"captions-slide{slide_idx}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=f"/slide[{slide_idx}]",
                current_value="media present, captions unverifiable",
                plain_impact=self.meta.plain_impact,
                why_human_needed="Verify captions are embedded; cannot detect from OOXML alone.",
            )


register_rule(CaptionsMediaRule())
