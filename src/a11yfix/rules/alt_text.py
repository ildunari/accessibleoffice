"""Rules: missing or low-quality alt text on pictures and image-filled shapes.

WCAG 1.1.1 (Non-text Content). Severity: Error.

OOXML elements inspected (PPT):
  - p:pic / p:nvPicPr / p:cNvPr  (picture: @descr or @title)
  - p:sp with p:spPr/a:blipFill / p:nvSpPr / p:cNvPr  (image-filled shape)

OOXML elements inspected (Word):
  - w:drawing/wp:inline (or wp:anchor) / a:graphic / a:graphicData
    For pictures: pic:pic / pic:nvPicPr / pic:cNvPr (@descr)
    Word also has w:docPr inside wp:inline/wp:anchor with @descr / @title.

Decorative flag: a:extLst / a:ext / adec:decorative (extension namespace 2017).
If decorative=1 we treat alt text as not required.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import (
    BaseRule,
    DocumentHandle,
    RuleMeta,
    SingleShotFix,
    register_rule,
)


def _is_decorative(cnvpr: object) -> bool:
    """Walk the cNvPr's extLst for adec:decorative='1'."""
    extlst = cnvpr.find(qn("a:extLst"))  # type: ignore[union-attr]
    if extlst is None:
        return False
    for ext in extlst.findall(qn("a:ext")):
        decoratives = ext.findall(qn("adec:decorative"))
        for d in decoratives:
            if d.get("val") == "1":
                return True
    return False


_IMAGE_EXTS = r"png|jpe?g|gif|svg|bmp|tiff?|webp|emf|wmf"
_FILENAME_RE = re.compile(rf"^[A-Za-z0-9 _.\-]+\.(?:{_IMAGE_EXTS})$", re.IGNORECASE)
_PATH_RE = re.compile(rf"(^[A-Za-z]:[\\/]|[\\/].+\.(?:{_IMAGE_EXTS})$)", re.IGNORECASE)
_MISSING_AUTO_NAMES = {"picture", "image", "shape", "object"}
_QUALITY_AUTO_NAMES = {"chart", "diagram", "smartart", "timeline", "graphic"}
_AUTO_GENERATED_PHRASES = (
    "description automatically generated",
    "automatically generated description",
    "automatically generated alt text",
)


def _normalized_auto_name(text: str) -> str:
    return re.sub(r"\s+\d+$", "", text.strip().lower())


def _is_missing_alt(text: str) -> bool:
    """Empty, generic object auto-name, or inserted-image filename = missing."""
    t = text.strip()
    if not t:
        return True
    if not _PATH_RE.search(t) and _FILENAME_RE.match(t):
        return True
    return _normalized_auto_name(t) in _MISSING_AUTO_NAMES


def _alt_quality_reason(text: str) -> str | None:
    """Return why present alt text is low quality, or None if it is usable."""
    t = text.strip()
    if not t or _is_missing_alt(t):
        return None
    low = t.lower()
    if any(phrase in low for phrase in _AUTO_GENERATED_PHRASES):
        return "office_auto_generated"
    if _PATH_RE.search(t):
        return "local_file_path"
    if _FILENAME_RE.match(t):
        return "filename"
    if _normalized_auto_name(t) in _QUALITY_AUTO_NAMES:
        return "generic_object_label"
    return None


def _alt_text(cnvpr: object) -> str:
    descr = cnvpr.get("descr") or cnvpr.get("title") or ""  # type: ignore[union-attr]
    return descr.strip() if not _is_missing_alt(descr) else ""


def _has_blip_fill(el: object) -> bool:
    sp_pr = el.find(qn("p:spPr"))  # type: ignore[union-attr]
    if sp_pr is None:
        return False
    return sp_pr.find(qn("a:blipFill")) is not None


@dataclass
class _ImageTarget:
    shape_kind: str
    shape_id: str
    shape_name: str
    officecli_path: str
    alt_text: str
    cnv: object
    slide_index: int | None = None
    paragraph: int | None = None
    run: int | None = None


def _raw_alt(cnvpr: object) -> str:
    return (cnvpr.get("descr") or cnvpr.get("title") or "").strip()  # type: ignore[union-attr]


def _pptx_image_targets(doc: DocumentHandle) -> Iterable[_ImageTarget]:
    from a11yfix.ooxml.pptx_reader import PptxHandle

    assert isinstance(doc, PptxHandle)
    for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
        spTree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")
        if spTree is None:
            continue
        for pic in spTree.iter(qn("p:pic")):
            nv = pic.find(qn("p:nvPicPr"))
            if nv is None:
                continue
            cnv = nv.find(qn("p:cNvPr"))
            if cnv is None:
                continue
            shape_id = cnv.get("id") or "0"
            yield _ImageTarget(
                shape_kind="pic",
                shape_id=shape_id,
                shape_name=cnv.get("name") or "(unnamed)",
                officecli_path=f"/sld[{slide_idx}]/pic[@id={shape_id}]",
                alt_text=_raw_alt(cnv),
                cnv=cnv,
                slide_index=slide_idx,
            )
        for sp in spTree.iter(qn("p:sp")):
            if not _has_blip_fill(sp):
                continue
            nv = sp.find(qn("p:nvSpPr"))
            if nv is None:
                continue
            cnv = nv.find(qn("p:cNvPr"))
            if cnv is None:
                continue
            shape_id = cnv.get("id") or "0"
            yield _ImageTarget(
                shape_kind="sp",
                shape_id=shape_id,
                shape_name=cnv.get("name") or "(unnamed)",
                officecli_path=f"/sld[{slide_idx}]/sp[@id={shape_id}]",
                alt_text=_raw_alt(cnv),
                cnv=cnv,
                slide_index=slide_idx,
            )


def _docx_image_targets(doc: DocumentHandle) -> Iterable[_ImageTarget]:
    from a11yfix.ooxml.docx_reader import DocxHandle

    assert isinstance(doc, DocxHandle)
    wp_ns = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    pic_ns = "http://schemas.openxmlformats.org/drawingml/2006/picture"

    para_idx = 0
    for para in doc.body.findall(qn("w:p")):
        para_idx += 1
        runs = para.findall(qn("w:r"))
        for run_idx, run in enumerate(runs, start=1):
            drawings = run.findall(qn("w:drawing"))
            if not drawings:
                continue
            for drawing in drawings:
                doc_pr = None
                for child in drawing.iter():
                    if child.tag == f"{{{wp_ns}}}docPr":
                        doc_pr = child
                        break
                if doc_pr is None:
                    continue
                inner_cnv = None
                for child in drawing.iter():
                    if child.tag == f"{{{pic_ns}}}cNvPr":
                        inner_cnv = child
                        break
                if inner_cnv is not None and _is_decorative(inner_cnv):
                    continue
                yield _ImageTarget(
                    shape_kind="pic",
                    shape_id=doc_pr.get("id") or "0",
                    shape_name=doc_pr.get("name") or "(unnamed)",
                    officecli_path=f"/body/p[{para_idx}]/r[{run_idx}]",
                    alt_text=_raw_alt(doc_pr),
                    cnv=doc_pr,
                    paragraph=para_idx,
                    run=run_idx,
                )


class AltTextRule(BaseRule):
    meta = RuleMeta(
        rule_id="alt-text-missing",
        severity=Severity.ERROR,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=["1.1.1"],
        plain_impact="Screen readers cannot describe this image to users.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        if doc.file_format == FileFormat.PPTX:
            yield from self._detect_pptx(doc)
        else:
            yield from self._detect_docx(doc)

    # --- PPTX ---

    def _detect_pptx(self, doc: DocumentHandle) -> Iterable[Finding]:
        for target in _pptx_image_targets(doc):
            if _is_decorative(target.cnv) or not _is_missing_alt(target.alt_text):
                continue
            assert target.slide_index is not None
            yield Finding(
                id=f"alt-{target.slide_index}-{target.shape_kind}-{target.shape_id}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=target.officecli_path,
                current_value=target.alt_text,
                plain_impact=self.meta.plain_impact,
                extra={
                    "shape_kind": target.shape_kind,
                    "shape_name": target.shape_name,
                    "slide_index": target.slide_index,
                },
            )

    # --- DOCX ---

    def _detect_docx(self, doc: DocumentHandle) -> Iterable[Finding]:
        for target in _docx_image_targets(doc):
            if not _is_missing_alt(target.alt_text):
                continue
            assert target.paragraph is not None
            assert target.run is not None
            yield Finding(
                id=f"alt-p{target.paragraph}-r{target.run}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=target.officecli_path,
                current_value=target.alt_text,
                plain_impact=self.meta.plain_impact,
                extra={
                    "pic_id": target.shape_id,
                    "pic_name": target.shape_name,
                    "paragraph": target.paragraph,
                    "run": target.run,
                },
            )

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        return SingleShotFix(kind="alt-text", finding=finding, context=dict(finding.extra))


class AltTextQualityRule(BaseRule):
    meta = RuleMeta(
        rule_id="alt-text-generic",
        severity=Severity.WARNING,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=["1.1.1"],
        plain_impact="Screen readers may get unhelpful image descriptions.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        if doc.file_format == FileFormat.PPTX:
            yield from self._detect_pptx(doc)
        else:
            yield from self._detect_docx(doc)

    def _detect_pptx(self, doc: DocumentHandle) -> Iterable[Finding]:
        for target in _pptx_image_targets(doc):
            if _is_decorative(target.cnv):
                continue
            reason = _alt_quality_reason(target.alt_text)
            if reason is None:
                continue
            assert target.slide_index is not None
            yield Finding(
                id=f"alt-quality-{target.slide_index}-{target.shape_kind}-{target.shape_id}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=target.officecli_path,
                current_value=target.alt_text,
                plain_impact=self.meta.plain_impact,
                why_human_needed="Existing alt text is present but likely not descriptive.",
                extra={
                    "shape_kind": target.shape_kind,
                    "shape_name": target.shape_name,
                    "slide_index": target.slide_index,
                    "reason": reason,
                },
            )

    def _detect_docx(self, doc: DocumentHandle) -> Iterable[Finding]:
        for target in _docx_image_targets(doc):
            reason = _alt_quality_reason(target.alt_text)
            if reason is None:
                continue
            assert target.paragraph is not None
            assert target.run is not None
            yield Finding(
                id=f"alt-quality-p{target.paragraph}-r{target.run}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=target.officecli_path,
                current_value=target.alt_text,
                plain_impact=self.meta.plain_impact,
                why_human_needed="Existing alt text is present but likely not descriptive.",
                extra={
                    "pic_id": target.shape_id,
                    "pic_name": target.shape_name,
                    "paragraph": target.paragraph,
                    "run": target.run,
                    "reason": reason,
                },
            )

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        return SingleShotFix(kind="alt-text", finding=finding, context=dict(finding.extra))


register_rule(AltTextRule())
register_rule(AltTextQualityRule())
