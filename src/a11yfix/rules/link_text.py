"""Rule: descriptive hyperlink text.

WCAG 2.4.4 (Link Purpose, In Context). Severity: Warning.

Detect: hyperlinks whose visible text is empty, the bare URL, or a generic phrase.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import (
    BaseRule,
    DocumentHandle,
    RuleMeta,
    SingleShotFix,
    register_rule,
)

GENERIC_PHRASES = {
    "click here",
    "click",
    "here",
    "this link",
    "link",
    "read more",
    "more",
    "more info",
    "learn more",
    "details",
    "this",
}

URL_RE = re.compile(r"^(https?|ftp|mailto|tel)://?", re.IGNORECASE)


def _is_generic(text: str, *, url: str | None) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    if URL_RE.match(t) or (url and t == url.lower()):
        return True
    return t in GENERIC_PHRASES


class LinkTextRule(BaseRule):
    meta = RuleMeta(
        rule_id="link-text-generic",
        severity=Severity.WARNING,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=["2.4.4"],
        plain_impact="Screen reader users get lists of meaningless link labels.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        if doc.file_format == FileFormat.DOCX:
            yield from self._detect_docx(doc)
        else:
            yield from self._detect_pptx(doc)

    def _detect_docx(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.docx_reader import DocxHandle

        assert isinstance(doc, DocxHandle)
        para_idx = 0
        for p in doc.body.iter(qn("w:p")):
            para_idx += 1
            for h_idx, h in enumerate(p.iter(qn("w:hyperlink")), start=1):
                text = "".join(t.text or "" for t in h.iter(qn("w:t")))
                rel_id = h.get(qn("r:id"))
                # URL would require resolving the relationship; we only need it for the heuristic
                if not _is_generic(text, url=None):
                    continue
                yield Finding(
                    id=f"link-p{para_idx}-h{h_idx}",
                    rule_id=self.meta.rule_id,
                    severity=self.meta.severity,
                    wcag_sc=self.meta.wcag_sc,
                    officecli_path=f"/body/p[{para_idx}]/hyperlink[{h_idx}]",
                    current_value=text.strip(),
                    plain_impact=self.meta.plain_impact,
                    extra={"paragraph": para_idx, "rel_id": rel_id or ""},
                )

    def _detect_pptx(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            # Each <a:r> may carry an <a:rPr><a:hlinkClick/></a:rPr>
            for sp_idx, sp in enumerate(slide_xml.iter(qn("p:sp")), start=1):
                for r_idx, r in enumerate(sp.iter(qn("a:r")), start=1):
                    rPr = r.find(qn("a:rPr"))
                    if rPr is None:
                        continue
                    hlink = rPr.find(qn("a:hlinkClick"))
                    if hlink is None:
                        continue
                    t = r.find(qn("a:t"))
                    text = (t.text or "") if t is not None else ""
                    if not _is_generic(text, url=None):
                        continue
                    yield Finding(
                        id=f"link-sld{slide_idx}-sp{sp_idx}-r{r_idx}",
                        rule_id=self.meta.rule_id,
                        severity=self.meta.severity,
                        wcag_sc=self.meta.wcag_sc,
                        officecli_path=f"/sld[{slide_idx}]/sp[{sp_idx}]/p[1]/r[{r_idx}]",
                        current_value=text.strip(),
                        plain_impact=self.meta.plain_impact,
                        extra={"slide_index": slide_idx},
                    )

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        return SingleShotFix(kind="link-text", finding=finding, context=dict(finding.extra))


register_rule(LinkTextRule())
