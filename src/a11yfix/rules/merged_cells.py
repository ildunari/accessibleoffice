"""Rule: tables with merged cells flagged for human review (never auto-unmerge).

WCAG 1.3.1 (Info and Relationships). Severity: Warning.

gridSpan / vMerge in Word; merged cells in PPT a:tc with rowSpan/gridSpan.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.docx_paths import iter_table_refs
from a11yfix.ooxml.namespaces import qn
from a11yfix.ooxml.pptx_paths import ppt_table_ref
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule


class MergedCellsRule(BaseRule):
    meta = RuleMeta(
        rule_id="table-merged-cells",
        severity=Severity.WARNING,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=["1.3.1"],
        plain_impact="Merged cells confuse screen readers about row/column relationships.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        if doc.file_format == FileFormat.DOCX:
            from a11yfix.ooxml.docx_reader import DocxHandle

            assert isinstance(doc, DocxHandle)
            for tbl_ref in iter_table_refs(doc.body):
                tbl = tbl_ref.element
                merged = False
                for tr in tbl.findall(qn("w:tr")):
                    cells = tr.findall(qn("w:tc"))
                    if any(_word_cell_is_merged(tc) for tc in cells):
                        merged = True
                        break
                if merged:
                    yield Finding(
                        id=f"merged-{_path_slug(tbl_ref.path)}",
                        rule_id=self.meta.rule_id,
                        severity=self.meta.severity,
                        wcag_sc=self.meta.wcag_sc,
                        officecli_path=tbl_ref.path,
                        current_value="contains merged cells",
                        plain_impact=self.meta.plain_impact,
                        why_human_needed="Splitting may break author intent — defer to human.",
                    )
        else:
            from a11yfix.ooxml.pptx_reader import PptxHandle

            assert isinstance(doc, PptxHandle)
            for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
                sp_tree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")
                if sp_tree is None:
                    continue
                for tbl in slide_xml.iter(qn("a:tbl")):
                    merged = False
                    for tc in tbl.iter(qn("a:tc")):
                        if _span_gt_one(tc.get("gridSpan")) or _span_gt_one(tc.get("rowSpan")):
                            merged = True
                            break
                        if tc.get("hMerge") == "1" or tc.get("vMerge") == "1":
                            merged = True
                            break
                    if merged:
                        table_ref = ppt_table_ref(slide_idx=slide_idx, sp_tree=sp_tree, tbl=tbl)
                        if table_ref is None:
                            continue
                        yield Finding(
                            id=f"merged-slide{slide_idx}-tbl{table_ref.shape_id}",
                            rule_id=self.meta.rule_id,
                            severity=self.meta.severity,
                            wcag_sc=self.meta.wcag_sc,
                            officecli_path=table_ref.path,
                            current_value="contains merged cells",
                            plain_impact=self.meta.plain_impact,
                            why_human_needed="Splitting may break author intent — defer to human.",
                        )


def _word_cell_is_merged(tc: object) -> bool:
    tcPr = tc.find(qn("w:tcPr"))
    if tcPr is None:
        return False
    grid_span = tcPr.find(qn("w:gridSpan"))
    if grid_span is not None and _span_gt_one(grid_span.get(qn("w:val")) or grid_span.get("val")):
        return True
    return tcPr.find(qn("w:vMerge")) is not None


def _span_gt_one(value: str | None) -> bool:
    try:
        return int(value or "1") > 1
    except ValueError:
        return bool(value)


def _path_slug(path: str) -> str:
    return path.strip("/").replace("/", "-").replace("[", "").replace("]", "")


register_rule(MergedCellsRule())
