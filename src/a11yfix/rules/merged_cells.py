"""Rule: tables with merged cells flagged for human review (never auto-unmerge).

WCAG 1.3.1 (Info and Relationships). Severity: Warning.

gridSpan / vMerge in Word; merged cells in PPT a:tc with rowSpan/gridSpan.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
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
            for tbl_idx, tbl in enumerate(doc.body.iter(qn("w:tbl")), start=1):
                merged = False
                for tc in tbl.iter(qn("w:tc")):
                    tcPr = tc.find(qn("w:tcPr"))
                    if tcPr is None:
                        continue
                    if tcPr.find(qn("w:gridSpan")) is not None:
                        merged = True
                        break
                    if tcPr.find(qn("w:vMerge")) is not None:
                        merged = True
                        break
                if merged:
                    yield Finding(
                        id=f"merged-tbl-{tbl_idx}",
                        rule_id=self.meta.rule_id,
                        severity=self.meta.severity,
                        wcag_sc=self.meta.wcag_sc,
                        officecli_path=f"/body/tbl[{tbl_idx}]",
                        current_value="contains merged cells",
                        plain_impact=self.meta.plain_impact,
                        why_human_needed="Splitting may break author intent — defer to human.",
                    )
        else:
            from a11yfix.ooxml.pptx_reader import PptxHandle

            assert isinstance(doc, PptxHandle)
            for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
                for tbl_idx, tbl in enumerate(slide_xml.iter(qn("a:tbl")), start=1):
                    merged = False
                    for tc in tbl.iter(qn("a:tc")):
                        if tc.get("gridSpan") or tc.get("rowSpan"):
                            merged = True
                            break
                        if tc.get("hMerge") == "1" or tc.get("vMerge") == "1":
                            merged = True
                            break
                    if merged:
                        yield Finding(
                            id=f"merged-sld{slide_idx}-tbl{tbl_idx}",
                            rule_id=self.meta.rule_id,
                            severity=self.meta.severity,
                            wcag_sc=self.meta.wcag_sc,
                            officecli_path=f"/sld[{slide_idx}]/table[{tbl_idx}]",
                            current_value="contains merged cells",
                            plain_impact=self.meta.plain_impact,
                            why_human_needed="Splitting may break author intent — defer to human.",
                        )


register_rule(MergedCellsRule())
