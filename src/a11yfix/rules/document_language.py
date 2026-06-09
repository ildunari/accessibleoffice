"""Rule: document language is set.

WCAG 3.1.1 (Language of Page). Severity: Tip.

Word: w:settings/w:themeFontLang or default lang in styles.xml/w:rPrDefault/w:rPr/w:lang.
PowerPoint: presentation.xml/p:defaultTextStyle/.../a:rPr/@lang or a:defRPr/@lang.

We require user opt-in for the deterministic fix (--default-lang flag).
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import (
    BaseRule,
    DocumentHandle,
    RuleMeta,
    register_rule,
)


class DocumentLanguageRule(BaseRule):
    meta = RuleMeta(
        rule_id="document-language-missing",
        severity=Severity.TIP,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=["3.1.1"],
        plain_impact="Screen readers may use the wrong pronunciation for this document.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        lang = ""
        if doc.file_format == FileFormat.DOCX:
            from a11yfix.ooxml.docx_reader import DocxHandle

            assert isinstance(doc, DocxHandle)
            styles = doc.styles_xml
            if styles is not None:
                lang_el = styles.find(
                    f"{qn('w:docDefaults')}/{qn('w:rPrDefault')}/{qn('w:rPr')}/{qn('w:lang')}"
                )
                if lang_el is not None:
                    lang = lang_el.get(qn("w:val")) or ""
            settings = doc.settings_xml
            if not lang and settings is not None:
                tfl = settings.find(qn("w:themeFontLang"))
                if tfl is not None:
                    lang = tfl.get(qn("w:val")) or ""
        else:
            from a11yfix.ooxml.pptx_reader import PptxHandle

            assert isinstance(doc, PptxHandle)
            # Scope to the canonical location (p:defaultTextStyle/a:lvlNpPr/
            # a:defRPr/@lang). Iterating the whole presentation element could
            # pick up an unrelated defRPr and misreport the default language.
            pres = doc.pptx.element
            default_style = pres.find(qn("p:defaultTextStyle"))
            if default_style is not None:
                for rpr in default_style.iter(qn("a:defRPr")):
                    if rpr.get("lang"):
                        lang = rpr.get("lang") or ""
                        break

        if lang.strip():
            return
        path = (
            "/document/settings/themeFontLang"
            if doc.file_format == FileFormat.DOCX
            else "/presentation/defaultTextStyle"
        )
        yield Finding(
            id="doc-lang-missing",
            rule_id=self.meta.rule_id,
            severity=self.meta.severity,
            wcag_sc=self.meta.wcag_sc,
            officecli_path=path,
            current_value="",
            plain_impact=self.meta.plain_impact,
            why_human_needed="Default language is opt-in (use --default-lang)",
        )

    # No deterministic fix without --default-lang opt-in; fixer-side reads CLI flag.


register_rule(DocumentLanguageRule())
