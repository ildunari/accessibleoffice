"""Rule: heading structure in Word — skipped levels and "fake" headings.

WCAG 1.3.1, 2.4.6, 2.4.10. Severity: Warning.

Skipped levels: e.g. Heading 1 → Heading 3 with no Heading 2 between.
Fake headings: paragraphs styled bold + larger but using Normal style.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.docx_paths import iter_paragraph_refs
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule

HEADING_RE = re.compile(r"^Heading\s*([1-9])$")


def _heading_level(p: object) -> int | None:
    pPr = p.find(qn("w:pPr"))  # type: ignore[union-attr]
    if pPr is None:
        return None
    pStyle = pPr.find(qn("w:pStyle"))
    if pStyle is None:
        return None
    val = pStyle.get(qn("w:val")) or ""
    m = HEADING_RE.match(val)
    if m:
        return int(m.group(1))
    return None


def _looks_like_fake_heading(p: object) -> bool:
    """Bold + larger than body, single short line, not already a real heading."""
    runs = list(p.iter(qn("w:r")))  # type: ignore[union-attr]
    if not runs:
        return False
    text = "".join(t.text or "" for t in p.iter(qn("w:t")))  # type: ignore[union-attr]
    if not text.strip() or len(text) > 120:
        return False
    bold_count = 0
    for r in runs:
        rPr = r.find(qn("w:rPr"))
        if rPr is not None and rPr.find(qn("w:b")) is not None:
            bold_count += 1
    return bold_count == len(runs)


class HeadingStructureRule(BaseRule):
    meta = RuleMeta(
        rule_id="heading-structure",
        severity=Severity.WARNING,
        formats={FileFormat.DOCX},
        wcag_sc=["1.3.1", "2.4.6", "2.4.10"],
        plain_impact="Skipped heading levels confuse screen reader navigation.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.docx_reader import DocxHandle

        assert isinstance(doc, DocxHandle)
        last_level: int | None = None
        for para_ref in iter_paragraph_refs(doc.body):
            p = para_ref.element
            level = _heading_level(p)
            if level is not None:
                if last_level is not None and level - last_level > 1:
                    yield Finding(
                        id=f"hdr-skip-{_path_slug(para_ref.path)}",
                        rule_id=self.meta.rule_id,
                        severity=self.meta.severity,
                        wcag_sc=self.meta.wcag_sc,
                        officecli_path=para_ref.path,
                        current_value=f"H{level}",
                        plain_impact=self.meta.plain_impact,
                        why_human_needed=f"Heading skipped from H{last_level} to H{level}",
                        extra={"prev_level": last_level, "this_level": level},
                    )
                last_level = level
            elif _looks_like_fake_heading(p):
                yield Finding(
                    id=f"hdr-fake-{_path_slug(para_ref.path)}",
                    rule_id=self.meta.rule_id,
                    severity=Severity.TIP,
                    wcag_sc=self.meta.wcag_sc,
                    officecli_path=para_ref.path,
                    current_value="bold paragraph, no Heading style",
                    plain_impact="This looks like a heading but isn't tagged as one.",
                    why_human_needed="May or may not be a heading — needs human confirmation.",
                )


def _path_slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")


register_rule(HeadingStructureRule())
