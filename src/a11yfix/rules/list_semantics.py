"""Rule: real list semantics (w:numPr) vs typed-bullet pseudo-lists.

WCAG 1.3.1. Severity: Tip.

Detect Word paragraphs that start with bullet glyphs (•, -, *, –) or manual
numbering ("1.", "a)", "iv.") but have no w:numPr.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.docx_paths import iter_paragraph_refs
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule

BULLET_RE = re.compile(r"^\s*[•·▪►–\-*●]\s+")
# Manual numbering: "1." / "1)" / "(1)" / "a." / "a)" / "iv." etc.
NUMBERED_RE = re.compile(r"^\s*\(?(\d{1,3}|[a-z]|[ivxlc]{1,5})[.)]\s+", re.IGNORECASE)


# Built-in Word heading/title styleIds: numbered paragraphs carrying these are
# section headings, never fake list items.
_HEADING_STYLE_RE = re.compile(r"^(Heading\d|Title|Subtitle)$", re.IGNORECASE)


def _is_heading_styled(p: object) -> bool:
    pPr = p.find(qn("w:pPr"))  # type: ignore[union-attr]
    if pPr is None:
        return False
    pStyle = pPr.find(qn("w:pStyle"))
    if pStyle is None:
        return False
    return bool(_HEADING_STYLE_RE.match(pStyle.get(qn("w:val")) or ""))


class ListSemanticsRule(BaseRule):
    meta = RuleMeta(
        rule_id="list-semantics-fake",
        severity=Severity.TIP,
        formats={FileFormat.DOCX},
        wcag_sc=["1.3.1"],
        plain_impact="Typed-bullet 'lists' aren't announced as lists by screen readers.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.docx_reader import DocxHandle

        assert isinstance(doc, DocxHandle)
        paras = []
        for para_ref in iter_paragraph_refs(doc.body):
            p = para_ref.element
            text = "".join(t.text or "" for t in p.iter(qn("w:t")))
            paras.append((para_ref, p, text))
        for i, (para_ref, p, text) in enumerate(paras):
            is_bullet = bool(BULLET_RE.match(text))
            is_numbered = bool(NUMBERED_RE.match(text))
            if not is_bullet and not is_numbered:
                continue
            if _is_heading_styled(p):
                # "1. Introduction" styled Heading1 is a section heading;
                # promoting it to a list would be the wrong fix.
                continue
            if is_numbered and not is_bullet:
                # A lone numbered paragraph ("1. Introduction") is usually a
                # heading, not a fake list — require an adjacent NUMBERED
                # paragraph before flagging. A bullet neighbor doesn't vouch:
                # a numbered heading followed by typed bullets is still a
                # heading, while real manual numbering comes in numbered runs.
                prev_match = (
                    i > 0
                    and bool(NUMBERED_RE.match(paras[i - 1][2]))
                    and not _is_heading_styled(paras[i - 1][1])
                )
                next_match = (
                    i + 1 < len(paras)
                    and bool(NUMBERED_RE.match(paras[i + 1][2]))
                    and not _is_heading_styled(paras[i + 1][1])
                )
                if not (prev_match or next_match):
                    continue
            pPr = p.find(qn("w:pPr"))
            if pPr is not None and pPr.find(qn("w:numPr")) is not None:
                continue
            yield Finding(
                id=f"fake-list-{_path_slug(para_ref.path)}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=para_ref.path,
                current_value=text[:60],
                plain_impact=self.meta.plain_impact,
                why_human_needed="Promoting to a real list may shift formatting; defer to human.",
            )


def _path_slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")


register_rule(ListSemanticsRule())
