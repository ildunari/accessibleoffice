"""Rule: missing alt text on pictures, shapes, charts, groups, smartart.

WCAG 1.1.1 (Non-text Content). Severity: Error.

OOXML elements inspected (PPT):
  - p:pic / p:nvPicPr / p:cNvPr  (picture: @descr or @title)
  - p:sp  / p:nvSpPr  / p:cNvPr  (shape, often used for inline images)
  - p:graphicFrame / p:nvGraphicFramePr / p:cNvPr  (chart, smartart, table)
  - p:grpSp / p:nvGrpSpPr / p:cNvPr  (group)

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


_FILENAME_RE = re.compile(r"^[A-Za-z0-9 _\-]+\.(png|jpe?g|gif|svg|bmp|tiff?|webp)$", re.IGNORECASE)
_AUTO_NAMES = {"picture", "image", "shape", "object", "chart", "diagram", "smartart"}


def _is_meaningless_alt(text: str) -> bool:
    """Filename-style or generic auto-name = treat as missing."""
    t = text.strip()
    if not t:
        return True
    if _FILENAME_RE.match(t):
        return True
    return t.lower().rstrip("0123456789 ") in _AUTO_NAMES


def _alt_text(cnvpr: object) -> str:
    descr = cnvpr.get("descr") or cnvpr.get("title") or ""  # type: ignore[union-attr]
    return descr.strip() if not _is_meaningless_alt(descr) else ""


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
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            spTree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")
            if spTree is None:
                continue
            for shape_kind, container_tag, nv_tag in [
                ("pic", "p:pic", "p:nvPicPr"),
                ("sp", "p:sp", "p:nvSpPr"),
                ("graphicFrame", "p:graphicFrame", "p:nvGraphicFramePr"),
                ("grpSp", "p:grpSp", "p:nvGrpSpPr"),
            ]:
                for el in spTree.iter(qn(container_tag)):
                    nv = el.find(qn(nv_tag))
                    if nv is None:
                        continue
                    cnv = nv.find(qn("p:cNvPr"))
                    if cnv is None:
                        continue
                    if _is_decorative(cnv):
                        continue
                    # Skip placeholder shapes — those are titles/body, not images.
                    if shape_kind == "sp":
                        nvSpPr = nv.find(qn("p:nvSpPr"))
                        if nvSpPr is not None:
                            nvPr = nv.find(qn("p:nvPr"))
                            if nvPr is not None and nvPr.find(qn("p:ph")) is not None:
                                continue
                        # Also skip plain text boxes that don't contain media — they're text content.
                        if (
                            shape_kind == "sp"
                            and el.find(qn("p:nvSpPr") + "/" + qn("p:cNvSpPr")) is not None
                        ):
                            # heuristic: has it got embedded media via blipFill? if not, skip
                            pass
                    if _alt_text(cnv):
                        continue
                    shape_id = cnv.get("id") or "0"
                    shape_name = cnv.get("name") or "(unnamed)"
                    # Use 1-based [@id=] addressing where possible
                    path = f"/sld[{slide_idx}]/{shape_kind}[@id={shape_id}]"
                    yield Finding(
                        id=f"alt-{slide_idx}-{shape_kind}-{shape_id}",
                        rule_id=self.meta.rule_id,
                        severity=self.meta.severity,
                        wcag_sc=self.meta.wcag_sc,
                        officecli_path=path,
                        current_value="",
                        plain_impact=self.meta.plain_impact,
                        extra={
                            "shape_kind": shape_kind,
                            "shape_name": shape_name,
                            "slide_index": slide_idx,
                        },
                    )

    # --- DOCX ---

    def _detect_docx(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.docx_reader import DocxHandle

        assert isinstance(doc, DocxHandle)
        # Iterate all w:drawing elements; each contains wp:inline or wp:anchor with w:docPr.
        wp_ns = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
        pic_ns = "http://schemas.openxmlformats.org/drawingml/2006/picture"

        # officecli addresses /body/p[N] as top-level paragraphs only — skip
        # paragraphs nested inside tables, headers, footers, etc.
        para_idx = 0
        for para in doc.body.findall(qn("w:p")):
            para_idx += 1
            runs = para.findall(qn("w:r"))
            for run_idx, run in enumerate(runs, start=1):
                drawings = run.findall(qn("w:drawing"))
                if not drawings:
                    continue
                for drawing in drawings:
                    # docPr lives at wp:inline/wp:docPr or wp:anchor/wp:docPr
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
                    descr = doc_pr.get("descr") or doc_pr.get("title") or ""
                    if descr.strip() and not _is_meaningless_alt(descr):
                        continue
                    pic_id = doc_pr.get("id") or "0"
                    pic_name = doc_pr.get("name") or "(unnamed)"
                    # officecli addresses Word pictures via the containing run
                    # (`/body/p[N]/r[K]`), not via a direct /pic[] child.
                    path = f"/body/p[{para_idx}]/r[{run_idx}]"
                    yield Finding(
                        id=f"alt-p{para_idx}-r{run_idx}",
                        rule_id=self.meta.rule_id,
                        severity=self.meta.severity,
                        wcag_sc=self.meta.wcag_sc,
                        officecli_path=path,
                        current_value="",
                        plain_impact=self.meta.plain_impact,
                        extra={
                            "pic_id": pic_id,
                            "pic_name": pic_name,
                            "paragraph": para_idx,
                            "run": run_idx,
                        },
                    )

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        return SingleShotFix(kind="alt-text", finding=finding, context=dict(finding.extra))


register_rule(AltTextRule())
