"""Rule: missing table header row.

WCAG 1.3.1 (Info and Relationships). Severity: Error.

Word: per-row w:trPr/w:tblHeader is what the Checker wants. tblLook/@firstRow
is a STYLE hint, not a header semantic — gotcha #5.

PowerPoint: a:tbl/a:tblPr/@firstRow="1" plus the table style ID controls header
visual styling, but the structural header semantic comes from firstRow=1.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.docx_paths import iter_table_refs
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import (
    BaseRule,
    DocumentHandle,
    OfficecliOp,
    RuleMeta,
    register_rule,
)


def _row_has_tblheader(tr: object) -> bool:
    trPr = tr.find(qn("w:trPr"))  # type: ignore[union-attr]
    if trPr is None:
        return False
    tbl_header = trPr.find(qn("w:tblHeader"))
    if tbl_header is None:
        return False
    val = (tbl_header.get(qn("w:val")) or tbl_header.get("val") or "").lower()
    return val not in {"0", "false", "off"}


def _row_appears_visually_header(tr: object) -> bool:
    """Heuristic for stage-2 deterministic fix: bold text or distinct fill."""
    # Bold runs in any cell?
    for r in tr.iter(qn("w:r")):  # type: ignore[union-attr]
        rPr = r.find(qn("w:rPr"))
        if rPr is not None and rPr.find(qn("w:b")) is not None:
            return True
    # Cell shading?
    for tc in tr.iter(qn("w:tc")):  # type: ignore[union-attr]
        tcPr = tc.find(qn("w:tcPr"))
        if tcPr is not None:
            shd = tcPr.find(qn("w:shd"))
            if shd is not None and shd.get(qn("w:fill")) not in (None, "auto", "FFFFFF"):
                return True
    return False


def _ppt_row_appears_visually_header(tr: object) -> bool:
    for r in tr.iter(qn("a:r")):  # type: ignore[union-attr]
        rPr = r.find(qn("a:rPr"))
        if rPr is not None and rPr.get("b") == "1":
            return True
    for tc in tr.findall(qn("a:tc")):  # type: ignore[union-attr]
        tcPr = tc.find(qn("a:tcPr"))
        if tcPr is not None and tcPr.find(qn("a:solidFill")) is not None:
            return True
    return False


class TableHeaderRule(BaseRule):
    meta = RuleMeta(
        rule_id="table-header-missing",
        severity=Severity.ERROR,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=["1.3.1"],
        plain_impact="Screen readers can't announce which cells are headers.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        if doc.file_format == FileFormat.DOCX:
            yield from self._detect_docx(doc)
        else:
            yield from self._detect_pptx(doc)

    def _detect_docx(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.docx_reader import DocxHandle

        assert isinstance(doc, DocxHandle)
        for tbl_ref in iter_table_refs(doc.body):
            tbl = tbl_ref.element
            rows = list(tbl.findall(qn("w:tr")))
            if not rows:
                continue
            first = rows[0]
            if len(rows) < 2 or len(first.findall(qn("w:tc"))) < 2:
                continue
            if _row_has_tblheader(first):
                continue
            yield Finding(
                id=f"tbl-hdr-{_path_slug(tbl_ref.path)}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=f"{tbl_ref.path}/tr[1]",
                current_value="",
                plain_impact=self.meta.plain_impact,
                extra={
                    "table_index": tbl_ref.index,
                    "table_path": tbl_ref.path,
                    "visually_header": _row_appears_visually_header(first),
                },
            )

    def _detect_pptx(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            for tbl_idx, tbl in enumerate(slide_xml.iter(qn("a:tbl")), start=1):
                rows = list(tbl.findall(qn("a:tr")))
                if not rows or len(rows) < 2 or len(rows[0].findall(qn("a:tc"))) < 2:
                    continue
                tblPr = tbl.find(qn("a:tblPr"))
                first_row = tblPr.get("firstRow") if tblPr is not None else None
                if first_row == "1":
                    continue
                yield Finding(
                    id=f"sld{slide_idx}-tbl{tbl_idx}-hdr",
                    rule_id=self.meta.rule_id,
                    severity=self.meta.severity,
                    wcag_sc=self.meta.wcag_sc,
                    officecli_path=f"/sld[{slide_idx}]/table[{tbl_idx}]",
                    current_value="firstRow=0",
                    plain_impact=self.meta.plain_impact,
                    extra={
                        "slide_index": slide_idx,
                        "table_index": tbl_idx,
                        "visually_header": _ppt_row_appears_visually_header(rows[0]),
                    },
                )

    def fix_deterministic(self, finding: Finding, doc: DocumentHandle) -> list[OfficecliOp] | None:
        # Only auto-fix when the row visually looks like a header.
        if doc.file_format == FileFormat.DOCX:
            if not finding.extra.get("visually_header"):
                return None
            return [
                OfficecliOp(
                    verb="set",
                    path=finding.officecli_path,
                    props={"header": "true"},
                )
            ]
        if not finding.extra.get("visually_header"):
            return None
        return [
            OfficecliOp(
                verb="set",
                path=finding.officecli_path,
                props={"firstRow": "1"},
            )
        ]


def _path_slug(path: str) -> str:
    return path.strip("/").replace("/", "-").replace("[", "").replace("]", "")


register_rule(TableHeaderRule())
