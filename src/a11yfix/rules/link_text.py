"""Rule: descriptive hyperlink text.

WCAG 2.4.4 (Link Purpose, In Context). Severity: Warning.

Detect: hyperlinks whose visible text is empty, the bare URL, or a generic phrase.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.docx_paths import iter_paragraph_refs
from a11yfix.ooxml.namespaces import qn
from a11yfix.ooxml.pptx_paths import ppt_target_ref
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
    "this article",
    "this document",
    "this file",
    "this help center article",
    "this page",
    "this pdf",
    "this resource",
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
        rels = _docx_rels(doc)
        for para_ref in iter_paragraph_refs(doc.body):
            p = para_ref.element
            para_text = "".join(t.text or "" for t in p.iter(qn("w:t"))).strip()
            for h_idx, h in enumerate(p.iter(qn("w:hyperlink")), start=1):
                text = "".join(t.text or "" for t in h.iter(qn("w:t")))
                rel_id = h.get(qn("r:id")) or ""
                url = rels.get(rel_id, "") if rel_id else ""
                if not _is_generic(text, url=url):
                    continue
                yield Finding(
                    id=f"link-{_path_slug(para_ref.path)}-h{h_idx}",
                    rule_id=self.meta.rule_id,
                    severity=self.meta.severity,
                    wcag_sc=self.meta.wcag_sc,
                    officecli_path=f"{para_ref.path}/hyperlink[{h_idx}]",
                    current_value=text.strip(),
                    plain_impact=self.meta.plain_impact,
                    extra={
                        "paragraph_path": para_ref.path,
                        "rel_id": rel_id,
                        "url": url,
                        "paragraph_text": para_text,
                    },
                )

    def _detect_pptx(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        slide_rels = _pptx_slide_rels(doc)
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            rels = slide_rels.get(slide_idx, {})
            sp_tree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")
            if sp_tree is None:
                continue
            for sp in slide_xml.iter(qn("p:sp")):
                sp_ref = ppt_target_ref(
                    slide_idx=slide_idx,
                    sp_tree=sp_tree,
                    element=sp,
                    element_name="shape",
                    cnv_path=f"{qn('p:nvSpPr')}/{qn('p:cNvPr')}",
                )
                if sp_ref is None:
                    continue
                sp_text = "".join(t.text or "" for t in sp.iter(qn("a:t"))).strip()
                for p_idx, para in enumerate(sp.iter(qn("a:p")), start=1):
                    for r_idx, r in enumerate(para.findall(qn("a:r")), start=1):
                        rPr = r.find(qn("a:rPr"))
                        if rPr is None:
                            continue
                        hlink = rPr.find(qn("a:hlinkClick"))
                        if hlink is None:
                            continue
                        rel_id = hlink.get(qn("r:id")) or ""
                        url = rels.get(rel_id, "") if rel_id else ""
                        t = r.find(qn("a:t"))
                        text = (t.text or "") if t is not None else ""
                        if not _is_generic(text, url=url):
                            continue
                        yield Finding(
                            id=(
                                f"link-slide{slide_idx}-shape{sp_ref.shape_id}"
                                f"-p{p_idx}-r{r_idx}"
                            ),
                            rule_id=self.meta.rule_id,
                            severity=self.meta.severity,
                            wcag_sc=self.meta.wcag_sc,
                            officecli_path=f"{sp_ref.path}/p[{p_idx}]/r[{r_idx}]",
                            current_value=text.strip(),
                            plain_impact=self.meta.plain_impact,
                            extra={
                                "slide_index": slide_idx,
                                "rel_id": rel_id,
                                "url": url,
                                "shape_text": sp_text,
                            },
                        )

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        return SingleShotFix(kind="link-text", finding=finding, context=dict(finding.extra))


def _docx_rels(doc: DocumentHandle) -> dict[str, str]:
    """rId → target URL for the main document part (hyperlinks)."""
    from a11yfix.ooxml.docx_reader import DocxHandle

    if not isinstance(doc, DocxHandle):
        return {}
    out: dict[str, str] = {}
    try:
        for rel in doc.docx.part.rels.values():
            if "hyperlink" in str(getattr(rel, "reltype", "")):
                out[rel.rId] = str(rel.target_ref or "")
    except Exception:
        return {}
    return out


def _pptx_slide_rels(doc: DocumentHandle) -> dict[int, dict[str, str]]:
    """slide_idx (1-based) → {rId: url} for hyperlink relationships."""
    from a11yfix.ooxml.pptx_reader import PptxHandle

    if not isinstance(doc, PptxHandle):
        return {}
    out: dict[int, dict[str, str]] = {}
    try:
        for idx, slide in enumerate(doc.pptx.slides, start=1):
            mp: dict[str, str] = {}
            for rel in slide.part.rels.values():
                if "hyperlink" in str(getattr(rel, "reltype", "")):
                    mp[rel.rId] = str(rel.target_ref or "")
            out[idx] = mp
    except Exception:
        return out
    return out


register_rule(LinkTextRule())


def _path_slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")
