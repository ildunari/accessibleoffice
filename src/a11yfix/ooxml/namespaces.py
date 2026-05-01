"""Central OOXML namespace map. All XML queries should use this; never hardcode."""

from __future__ import annotations

NS: dict[str, str] = {
    # WordprocessingML
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    # PresentationML
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    # SpreadsheetML
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    # DrawingML (shared by all)
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    # Drawing extension namespace for the decorative flag (a:extLst → adec)
    "adec": "http://schemas.microsoft.com/office/drawing/2017/decorative",
    # Relationships
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    # Core / extended properties
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    # Mark-compatibility
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


def qn(prefix_local: str) -> str:
    """Convert "w:tblHeader" → "{http://...}tblHeader" for lxml."""
    prefix, local = prefix_local.split(":", 1)
    return f"{{{NS[prefix]}}}{local}"
