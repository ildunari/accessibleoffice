"""OfficeCLI-compatible PowerPoint path helpers."""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from a11yfix.ooxml.namespaces import qn


@dataclass(frozen=True)
class PptTargetRef:
    element: etree._Element
    path: str
    shape_id: str
    shape_name: str


def slide_path(slide_idx: int) -> str:
    return f"/slide[{slide_idx}]"


def ppt_target_ref(
    *,
    slide_idx: int,
    sp_tree: etree._Element,
    element: etree._Element,
    element_name: str,
    cnv_path: str,
) -> PptTargetRef | None:
    cnv = element.find(cnv_path)
    if cnv is None:
        return None
    shape_id = cnv.get("id") or ""
    if not shape_id:
        return None
    scope = _group_scope(slide_idx, sp_tree, element)
    if scope is None:
        return None
    return PptTargetRef(
        element=element,
        path=f"{scope}/{element_name}[@id={shape_id}]",
        shape_id=shape_id,
        shape_name=cnv.get("name") or "(unnamed)",
    )


def ppt_table_ref(
    *, slide_idx: int, sp_tree: etree._Element, tbl: etree._Element
) -> PptTargetRef | None:
    graphic_frame = _ancestor(tbl, qn("p:graphicFrame"))
    if graphic_frame is None:
        return None
    return ppt_target_ref(
        slide_idx=slide_idx,
        sp_tree=sp_tree,
        element=graphic_frame,
        element_name="table",
        cnv_path=f"{qn('p:nvGraphicFramePr')}/{qn('p:cNvPr')}",
    )


def _group_scope(slide_idx: int, sp_tree: etree._Element, element: etree._Element) -> str | None:
    groups: list[str] = []
    node = element.getparent()
    while node is not None and node is not sp_tree:
        if node.tag == qn("p:grpSp"):
            cnv = node.find(f"{qn('p:nvGrpSpPr')}/{qn('p:cNvPr')}")
            group_id = cnv.get("id") if cnv is not None else ""
            if not group_id:
                return None
            groups.append(f"group[@id={group_id}]")
        node = node.getparent()
    groups.reverse()
    return "/".join([slide_path(slide_idx), *groups])


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    node = element.getparent()
    while node is not None:
        if node.tag == tag:
            return node
        node = node.getparent()
    return None
