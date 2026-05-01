"""Read-only access to .pptx OOXML using python-pptx + lxml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree
from pptx import Presentation  # type: ignore[import-untyped]

from a11yfix.manifest import FileFormat


@dataclass
class PptxHandle:
    file_format: FileFormat
    path: str
    pptx: Any  # pptx.presentation.Presentation
    slides_xml: list[etree._Element]
    core_xml: etree._Element | None

    def root_xml(self) -> etree._Element:
        # PPT has no single root body; return presentation element.
        return self.pptx.element


def open_pptx(path: str | Path) -> PptxHandle:
    p = Path(path).resolve()
    pres = Presentation(str(p))
    slides_xml = [slide.element for slide in pres.slides]
    core_xml: etree._Element | None = None
    try:
        core_xml = pres.core_properties._element
    except Exception:
        core_xml = None
    return PptxHandle(
        file_format=FileFormat.PPTX,
        path=str(p),
        pptx=pres,
        slides_xml=slides_xml,
        core_xml=core_xml,
    )
