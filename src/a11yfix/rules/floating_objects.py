"""Rule: floating (anchored) images in Word that should usually be in-line for a11y.

WCAG 1.3.2 (Meaningful Sequence). Severity: Warning.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule


class FloatingObjectsRule(BaseRule):
    meta = RuleMeta(
        rule_id="floating-object",
        severity=Severity.WARNING,
        formats={FileFormat.DOCX},
        wcag_sc=["1.3.2"],
        plain_impact="Floating images may be skipped or read out of order by screen readers.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.docx_reader import DocxHandle

        assert isinstance(doc, DocxHandle)
        wp_ns = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
        para_idx = 0
        for p in doc.body.iter(qn("w:p")):
            para_idx += 1
            for d_idx, d in enumerate(p.iter(qn("w:drawing")), start=1):
                anchor = None
                for child in d.iter():
                    if child.tag == f"{{{wp_ns}}}anchor":
                        anchor = child
                        break
                if anchor is None:
                    continue
                yield Finding(
                    id=f"float-p{para_idx}-d{d_idx}",
                    rule_id=self.meta.rule_id,
                    severity=self.meta.severity,
                    wcag_sc=self.meta.wcag_sc,
                    officecli_path=f"/body/p[{para_idx}]/pic[{d_idx}]",
                    current_value="anchored (floating) image",
                    plain_impact=self.meta.plain_impact,
                    why_human_needed="Wrap behavior may be intentional; defer.",
                )


register_rule(FloatingObjectsRule())
