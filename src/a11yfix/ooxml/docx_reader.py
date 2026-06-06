"""Read-only access to .docx OOXML using python-docx + lxml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument  # type: ignore[import-untyped]
from lxml import etree

from a11yfix.manifest import FileFormat


@dataclass
class DocxHandle:
    file_format: FileFormat
    path: str
    docx: Any  # docx.document.Document
    tree: etree._ElementTree
    body: etree._Element
    core_xml: etree._Element | None
    settings_xml: etree._Element | None
    styles_xml: etree._Element | None

    def root_xml(self) -> etree._Element:
        return self.body


def open_docx(path: str | Path) -> DocxHandle:
    p = Path(path).resolve()
    doc = DocxDocument(str(p))
    body = doc.element.body
    tree = body.getroottree()
    # Core properties: python-docx exposes ._element on CoreProperties.
    core_xml: etree._Element | None = None
    try:
        core_xml = doc.core_properties._element
    except Exception:
        core_xml = None
    settings_xml: etree._Element | None = None
    try:
        settings_xml = doc.settings.element  # type: ignore[attr-defined]
    except Exception:
        try:
            settings_xml = doc.settings._element  # type: ignore[attr-defined]
        except Exception:
            try:
                settings_xml = doc.settings.part.element  # type: ignore[attr-defined]
            except Exception:
                settings_xml = None
    styles_xml: etree._Element | None = None
    try:
        styles_xml = doc.styles.element  # type: ignore[attr-defined]
    except Exception:
        try:
            styles_xml = doc.styles._element  # type: ignore[attr-defined]
        except Exception:
            try:
                styles_xml = doc.styles.part.element  # type: ignore[attr-defined]
            except Exception:
                styles_xml = None
    return DocxHandle(
        file_format=FileFormat.DOCX,
        path=str(p),
        docx=doc,
        tree=tree,
        body=body,
        core_xml=core_xml,
        settings_xml=settings_xml,
        styles_xml=styles_xml,
    )
