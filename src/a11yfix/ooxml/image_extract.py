"""Extract embedded image bytes from .docx / .pptx for stage-3 alt-text.

Resolves picture elements to the binary blob inside the OOXML zip via the
relationship table.  Returns (bytes, mime_type) or None if not found.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from a11yfix.manifest import FileFormat, Finding
from a11yfix.ooxml.docx_paths import iter_paragraph_refs, iter_run_refs
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import DocumentHandle

_PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
}


def _mime_for(target: str) -> str:
    return _MIME_BY_EXT.get(Path(target).suffix.lower(), "image/png")


def extract_image_for_finding(
    doc: DocumentHandle, finding: Finding
) -> tuple[bytes, str] | None:
    """Best-effort: return (image_bytes, mime) for a `alt-text-missing` finding."""
    if doc.file_format == FileFormat.DOCX:
        return _extract_docx(doc, finding)
    if doc.file_format == FileFormat.PPTX:
        return _extract_pptx(doc, finding)
    return None


def _extract_docx(doc: DocumentHandle, finding: Finding) -> tuple[bytes, str] | None:
    from a11yfix.ooxml.docx_reader import DocxHandle

    if not isinstance(doc, DocxHandle):
        return None
    drawings: list[Any] = []
    run_path = str(finding.extra.get("run_path") or "")
    if run_path:
        drawings = _docx_drawings_for_run_path(doc, run_path)
    para_idx = int(finding.extra.get("paragraph") or 0)
    run_idx = int(finding.extra.get("run") or 0)
    if not drawings and run_idx > 0:
        paras = doc.body.findall(qn("w:p"))
        if 0 < para_idx <= len(paras):
            runs = paras[para_idx - 1].findall(qn("w:r"))
            if run_idx <= len(runs):
                drawings = runs[run_idx - 1].findall(qn("w:drawing"))
    if not drawings:
        pic_id = str(finding.extra.get("pic_id") or "")
        drawings = _docx_drawings_for_pic_id(doc, pic_id)
    if not drawings:
        drawings = list(doc.body.iter(qn("w:drawing")))
    if not drawings:
        return None
    drawing = drawings[0]
    blip = None
    for child in drawing.iter():
        if etree.QName(child.tag).localname == "blip":
            blip = child
            break
    if blip is None:
        return None
    embed = blip.get(f"{{{_R_NS}}}embed") or blip.get("embed") or ""
    if not embed:
        return None
    # Resolve via document part rels
    target = ""
    try:
        for rel in doc.docx.part.rels.values():
            if rel.rId == embed:
                target = str(rel.target_ref or "")
                break
    except Exception:
        return None
    if not target:
        return None
    return _read_zip_member(doc.path, "word", target)


def _docx_drawings_for_run_path(doc: DocumentHandle, run_path: str) -> list[Any]:
    from a11yfix.ooxml.docx_reader import DocxHandle

    if not isinstance(doc, DocxHandle):
        return []
    for para_ref in iter_paragraph_refs(doc.body):
        for run_ref in iter_run_refs(para_ref.element, para_ref.path):
            if run_ref.path == run_path:
                return list(run_ref.element.findall(qn("w:drawing")))
    return []


def _docx_drawings_for_pic_id(doc: DocumentHandle, pic_id: str) -> list[Any]:
    if not pic_id:
        return []
    drawings: list[Any] = []
    for drawing in doc.body.iter(qn("w:drawing")):
        for child in drawing.iter():
            if etree.QName(child.tag).localname == "docPr" and child.get("id") == pic_id:
                drawings.append(drawing)
                break
    return drawings


def _extract_pptx(doc: DocumentHandle, finding: Finding) -> tuple[bytes, str] | None:
    from a11yfix.ooxml.pptx_reader import PptxHandle

    if not isinstance(doc, PptxHandle):
        return None
    slide_idx = int(finding.extra.get("slide_index", 0))
    if slide_idx <= 0 or slide_idx > len(doc.slides_xml):
        return None
    slide_xml = doc.slides_xml[slide_idx - 1]
    shape_id = str(finding.officecli_path).rsplit("=", 1)[-1].rstrip("]")
    target = ""
    # Find p:pic or image-filled p:sp with matching cNvPr@id then read its blip.
    candidates = [
        (qn("p:pic"), f"{qn('p:nvPicPr')}/{qn('p:cNvPr')}"),
        (qn("p:sp"), f"{qn('p:nvSpPr')}/{qn('p:cNvPr')}"),
    ]
    for tag, cnv_path in candidates:
        for el in slide_xml.iter(tag):
            cnv = el.find(cnv_path)
            if cnv is None:
                continue
            if shape_id and cnv.get("id") != shape_id:
                continue
            blip = None
            for child in el.iter():
                if etree.QName(child.tag).localname == "blip":
                    blip = child
                    break
            if blip is None:
                continue
            embed = blip.get(f"{{{_R_NS}}}embed") or blip.get("embed") or ""
            if not embed:
                continue
            try:
                slide_part = doc.pptx.slides[slide_idx - 1].part
                for rel in slide_part.rels.values():
                    if rel.rId == embed:
                        target = str(rel.target_ref or "")
                        break
            except Exception:
                return None
            if target:
                break
        if target:
            break
    if not target:
        return None
    # PPT slide rels typically target ../media/imageN.png — normalize to ppt/media/...
    member = target.lstrip("./")
    if member.startswith("../"):
        member = member[3:]
    if not member.startswith("ppt/"):
        member = f"ppt/{member}"
    return _read_zip_member_abs(doc.path, member)


def _pic_index_in_para(finding: Finding) -> int:
    # officecli_path looks like /body/p[N]/pic[K]
    try:
        seg = str(finding.officecli_path).rsplit("/", 1)[-1]
        if seg.startswith("pic[") and seg.endswith("]"):
            return int(seg[4:-1])
    except Exception:
        pass
    return 1


def _read_zip_member(
    docx_path: str, prefix: str, target: str
) -> tuple[bytes, str] | None:
    member = target.lstrip("./")
    if member.startswith("../"):
        member = member[3:]
    if not member.startswith(f"{prefix}/"):
        member = f"{prefix}/{member}"
    return _read_zip_member_abs(docx_path, member)


def _read_zip_member_abs(zpath: str, member: str) -> tuple[bytes, str] | None:
    try:
        with zipfile.ZipFile(zpath) as zf:
            if member not in zf.namelist():
                # try without leading folder normalization
                cand = [n for n in zf.namelist() if n.endswith("/" + Path(member).name)]
                if not cand:
                    return None
                member = cand[0]
            data = zf.read(member)
    except (zipfile.BadZipFile, KeyError, OSError):
        return None
    return data, _mime_for(member)
