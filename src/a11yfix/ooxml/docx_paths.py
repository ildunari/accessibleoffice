"""Path-aware WordprocessingML traversal helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from lxml import etree

from a11yfix.ooxml.namespaces import qn


@dataclass(frozen=True)
class ParagraphRef:
    element: etree._Element
    path: str
    index: int


@dataclass(frozen=True)
class RunRef:
    element: etree._Element
    path: str
    index: int


@dataclass(frozen=True)
class TableRef:
    element: etree._Element
    path: str
    index: int


def iter_paragraph_refs(body: etree._Element) -> Iterable[ParagraphRef]:
    yield from _iter_block_paragraphs(body, "/body")


def iter_table_refs(body: etree._Element) -> Iterable[TableRef]:
    yield from _iter_block_tables(body, "/body")


def iter_run_refs(paragraph: etree._Element, paragraph_path: str) -> Iterable[RunRef]:
    run_idx = 0
    hyperlink_idx = 0
    for child in paragraph:
        if child.tag == qn("w:r"):
            run_idx += 1
            yield RunRef(child, f"{paragraph_path}/r[{run_idx}]", run_idx)
        elif child.tag == qn("w:hyperlink"):
            hyperlink_idx += 1
            link_run_idx = 0
            for link_child in child:
                if link_child.tag != qn("w:r"):
                    continue
                link_run_idx += 1
                yield RunRef(
                    link_child,
                    f"{paragraph_path}/hyperlink[{hyperlink_idx}]/r[{link_run_idx}]",
                    link_run_idx,
                )


def _iter_block_paragraphs(container: etree._Element, base_path: str) -> Iterable[ParagraphRef]:
    para_idx = 0
    tbl_idx = 0
    for child in container:
        if child.tag == qn("w:p"):
            para_idx += 1
            yield ParagraphRef(child, f"{base_path}/p[{para_idx}]", para_idx)
        elif child.tag == qn("w:tbl"):
            tbl_idx += 1
            yield from _iter_table_paragraphs(child, f"{base_path}/tbl[{tbl_idx}]")


def _iter_block_tables(container: etree._Element, base_path: str) -> Iterable[TableRef]:
    tbl_idx = 0
    for child in container:
        if child.tag != qn("w:tbl"):
            continue
        tbl_idx += 1
        table_path = f"{base_path}/tbl[{tbl_idx}]"
        yield TableRef(child, table_path, tbl_idx)
        for tr_idx, tr in enumerate(child.findall(qn("w:tr")), start=1):
            for tc_idx, tc in enumerate(tr.findall(qn("w:tc")), start=1):
                cell_path = f"{table_path}/tr[{tr_idx}]/tc[{tc_idx}]"
                yield from _iter_block_tables(tc, cell_path)


def _iter_table_paragraphs(table: etree._Element, table_path: str) -> Iterable[ParagraphRef]:
    for tr_idx, tr in enumerate(table.findall(qn("w:tr")), start=1):
        for tc_idx, tc in enumerate(tr.findall(qn("w:tc")), start=1):
            cell_path = f"{table_path}/tr[{tr_idx}]/tc[{tc_idx}]"
            yield from _iter_block_paragraphs(tc, cell_path)
