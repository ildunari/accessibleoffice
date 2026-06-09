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
    ".emf": "image/x-emf",
    ".wmf": "image/x-wmf",
}

# The only media types the Anthropic Messages API accepts for base64 image
# blocks. The API validates declared type against actual bytes and rejects
# mismatches with a 400, so callers must sniff — never trust the extension.
VISION_API_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)


def sniff_image(data: bytes) -> str | None:
    """Identify an image format from magic bytes. Returns a MIME type or None."""
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if len(data) >= 44 and data[40:44] == b" EMF":
        return "image/x-emf"
    if data[:4] == b"\xd7\xcd\xc6\x9a" or data[:4] == b"\x01\x00\x09\x00":
        return "image/x-wmf"
    head = data[:256].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:1024]):
        return "image/svg+xml"
    return None


def ensure_vision_compatible(data: bytes) -> tuple[bytes, str]:
    """Return (bytes, media_type) safe to send to the Anthropic vision API.

    PNG/JPEG/GIF/WebP pass through with their sniffed type. BMP/TIFF are
    converted to PNG via Pillow when available. Vector/metafile formats
    (EMF, WMF, SVG) and unidentifiable bytes raise ValueError so callers
    defer the finding instead of triggering a guaranteed API 400.
    """
    media = sniff_image(data)
    if media in VISION_API_MEDIA_TYPES:
        return data, media  # type: ignore[return-value]
    if media in ("image/bmp", "image/tiff"):
        try:
            import io

            from PIL import Image  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ValueError(
                f"{media} requires Pillow to convert for the vision API"
            ) from exc
        with Image.open(io.BytesIO(data)) as im:
            if im.mode not in ("RGB", "RGBA", "L"):
                im = im.convert("RGB")
            out = io.BytesIO()
            im.save(out, format="PNG")
        return out.getvalue(), "image/png"
    raise ValueError(
        f"unsupported image format for vision API: {media or 'unidentified bytes'}"
    )


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
    member = _strip_rel_prefixes(target)
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


def _strip_rel_prefixes(target: str) -> str:
    """Drop leading './' and '../' segments from a relationship target.

    str.lstrip("./") would strip *characters*, not prefixes, mangling names
    like '...dots.png'; strip explicit path segments instead.
    """
    member = target
    while True:
        if member.startswith("./"):
            member = member[2:]
        elif member.startswith("../"):
            member = member[3:]
        else:
            return member


def _read_zip_member(
    docx_path: str, prefix: str, target: str
) -> tuple[bytes, str] | None:
    member = _strip_rel_prefixes(target)
    if not member.startswith(f"{prefix}/"):
        member = f"{prefix}/{member}"
    return _read_zip_member_abs(docx_path, member)


def _read_zip_member_abs(zpath: str, member: str) -> tuple[bytes, str] | None:
    try:
        with zipfile.ZipFile(zpath) as zf:
            if member not in zf.namelist():
                # Fall back to a basename match, but stay inside the same
                # top-level part (word/ vs ppt/) so a hybrid package can't
                # hand back an image from the wrong document part.
                part_prefix = member.split("/", 1)[0] + "/"
                cand = [
                    n
                    for n in zf.namelist()
                    if n.endswith("/" + Path(member).name) and n.startswith(part_prefix)
                ]
                if not cand:
                    return None
                member = cand[0]
            data = zf.read(member)
    except (zipfile.BadZipFile, KeyError, OSError):
        return None
    return data, _mime_for(member)
