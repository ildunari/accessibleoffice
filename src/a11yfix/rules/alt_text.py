"""Rules: missing or low-quality alt text on pictures and image-filled shapes.

WCAG 1.1.1 (Non-text Content). Severity: Error.

OOXML elements inspected (PPT):
  - p:pic / p:nvPicPr / p:cNvPr  (picture: @descr or @title)
  - p:sp with p:spPr/a:blipFill / p:nvSpPr / p:cNvPr  (image-filled shape)
  - p:graphicFrame charts / SmartArt and p:grpSp groups

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
from a11yfix.ooxml.docx_paths import iter_paragraph_refs, iter_run_refs
from a11yfix.ooxml.namespaces import qn
from a11yfix.ooxml.pptx_paths import ppt_target_ref, slide_path
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
    paragraph_path: str | None = None
    run_path: str | None = None


def _raw_alt(cnvpr: object) -> str:
    return (cnvpr.get("descr") or cnvpr.get("title") or "").strip()  # type: ignore[union-attr]


def _shape_target(
    *,
    shape_kind: str,
    cnv: object,
    slide_idx: int,
    officecli_path: str,
) -> _ImageTarget:
    shape_id = cnv.get("id") or "0"  # type: ignore[union-attr]
    return _ImageTarget(
        shape_kind=shape_kind,
        shape_id=shape_id,
        shape_name=cnv.get("name") or "(unnamed)",  # type: ignore[union-attr]
        officecli_path=officecli_path,
        alt_text=_raw_alt(cnv),
        cnv=cnv,
        slide_index=slide_idx,
    )


def _graphic_frame_kind(graphic_frame: object) -> str | None:
    graphic_data = graphic_frame.find(f"{qn('a:graphic')}/{qn('a:graphicData')}")  # type: ignore[union-attr]
    if graphic_data is None:
        return None
    uri = (graphic_data.get("uri") or "").lower()
    if "chart" in uri:
        return "chart"
    if "diagram" in uri:
        return "smartArt"
    # Embedded tables have their own table-header rule and should not become
    # image-alt findings.
    return None


def _pptx_image_targets(doc: DocumentHandle) -> Iterable[_ImageTarget]:
    from a11yfix.ooxml.pptx_reader import PptxHandle

    assert isinstance(doc, PptxHandle)
    for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
        spTree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")
        if spTree is None:
            continue
        for pic in spTree.iter(qn("p:pic")):
            cnv = pic.find(f"{qn('p:nvPicPr')}/{qn('p:cNvPr')}")
            target_ref = ppt_target_ref(
                slide_idx=slide_idx,
                sp_tree=spTree,
                element=pic,
                element_name="picture",
                cnv_path=f"{qn('p:nvPicPr')}/{qn('p:cNvPr')}",
            )
            if target_ref is None or cnv is None:
                continue
            yield _shape_target(
                shape_kind="picture",
                cnv=cnv,
                slide_idx=slide_idx,
                officecli_path=target_ref.path,
            )
        for sp in spTree.iter(qn("p:sp")):
            if not _has_blip_fill(sp):
                continue
            cnv = sp.find(f"{qn('p:nvSpPr')}/{qn('p:cNvPr')}")
            target_ref = ppt_target_ref(
                slide_idx=slide_idx,
                sp_tree=spTree,
                element=sp,
                element_name="shape",
                cnv_path=f"{qn('p:nvSpPr')}/{qn('p:cNvPr')}",
            )
            if target_ref is None or cnv is None:
                continue
            yield _shape_target(
                shape_kind="shape",
                cnv=cnv,
                slide_idx=slide_idx,
                officecli_path=target_ref.path,
            )
        for graphic_frame in spTree.iter(qn("p:graphicFrame")):
            kind = _graphic_frame_kind(graphic_frame)
            if kind is None:
                continue
            nv = graphic_frame.find(qn("p:nvGraphicFramePr"))
            if nv is None:
                continue
            cnv = nv.find(qn("p:cNvPr"))
            if cnv is None:
                continue
            if kind == "smartArt":
                yield _shape_target(
                    shape_kind=kind,
                    cnv=cnv,
                    slide_idx=slide_idx,
                    officecli_path=slide_path(slide_idx),
                )
                continue
            target_ref = ppt_target_ref(
                slide_idx=slide_idx,
                sp_tree=spTree,
                element=graphic_frame,
                element_name="chart",
                cnv_path=f"{qn('p:nvGraphicFramePr')}/{qn('p:cNvPr')}",
            )
            if target_ref is None:
                continue
            yield _shape_target(
                shape_kind=kind,
                cnv=cnv,
                slide_idx=slide_idx,
                officecli_path=target_ref.path,
            )
        for group in spTree.iter(qn("p:grpSp")):
            cnv = group.find(f"{qn('p:nvGrpSpPr')}/{qn('p:cNvPr')}")
            target_ref = ppt_target_ref(
                slide_idx=slide_idx,
                sp_tree=spTree,
                element=group,
                element_name="group",
                cnv_path=f"{qn('p:nvGrpSpPr')}/{qn('p:cNvPr')}",
            )
            if target_ref is None or cnv is None:
                continue
            yield _shape_target(
                shape_kind="group",
                cnv=cnv,
                slide_idx=slide_idx,
                officecli_path=target_ref.path,
            )


def _docx_image_targets(doc: DocumentHandle) -> Iterable[_ImageTarget]:
    from a11yfix.ooxml.docx_reader import DocxHandle

    assert isinstance(doc, DocxHandle)
    wp_ns = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    pic_ns = "http://schemas.openxmlformats.org/drawingml/2006/picture"

    for para_ref in iter_paragraph_refs(doc.body):
        for run_ref in iter_run_refs(para_ref.element, para_ref.path):
            drawings = run_ref.element.findall(qn("w:drawing"))
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
                if _is_decorative(doc_pr) or (inner_cnv is not None and _is_decorative(inner_cnv)):
                    continue
                yield _ImageTarget(
                    shape_kind="pic",
                    shape_id=doc_pr.get("id") or "0",
                    shape_name=doc_pr.get("name") or "(unnamed)",
                    officecli_path=run_ref.path,
                    alt_text=_raw_alt(doc_pr),
                    cnv=doc_pr,
                    paragraph=para_ref.index if para_ref.path.startswith("/body/p[") else None,
                    run=run_ref.index if run_ref.path.startswith(f"{para_ref.path}/r[") else None,
                    paragraph_path=para_ref.path,
                    run_path=run_ref.path,
                )


def _smartart_deferred_message() -> str:
    return "OfficeCLI has no writable SmartArt alt-text path yet; set this manually in PowerPoint."


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
                why_human_needed=(
                    _smartart_deferred_message() if target.shape_kind == "smartArt" else None
                )
                or "",
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
            path_slug = re.sub(r"[^A-Za-z0-9]+", "-", target.officecli_path).strip("-")
            yield Finding(
                id=f"alt-{path_slug}",
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
                    "paragraph_path": target.paragraph_path,
                    "run_path": target.run_path,
                },
            )

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        if doc.file_format == FileFormat.PPTX and finding.extra.get("shape_kind") == "smartArt":
            return None
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
                why_human_needed=(
                    _smartart_deferred_message()
                    if target.shape_kind == "smartArt"
                    else "Existing alt text is present but likely not descriptive."
                ),
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
            path_slug = re.sub(r"[^A-Za-z0-9]+", "-", target.officecli_path).strip("-")
            yield Finding(
                id=f"alt-quality-{path_slug}",
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
                    "paragraph_path": target.paragraph_path,
                    "run_path": target.run_path,
                    "reason": reason,
                },
            )

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        if doc.file_format == FileFormat.PPTX and finding.extra.get("shape_kind") == "smartArt":
            return None
        return SingleShotFix(kind="alt-text", finding=finding, context=dict(finding.extra))


register_rule(AltTextRule())
register_rule(AltTextQualityRule())
