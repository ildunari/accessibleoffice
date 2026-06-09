"""Smoke tests that run real deterministic fix ops against the installed officecli.

These guard the officecli boundary: a finding's op must not just be *accepted*
by officecli, it must *persist* the intended change on disk. This is exactly the
class of bug that slipped through before — the document-title op targeted a path
officecli rejected ("Path not found"), so the fix silently no-op'd while the
manifest reported success and the file was still round-tripped.

Skipped automatically when officecli is not on PATH (e.g. CI without it).
"""

from __future__ import annotations

import shutil
import zipfile
from xml.etree import ElementTree as ET

import pytest

from a11yfix.fixers.deterministic import apply_deterministic_fixes
from a11yfix.ooxml.docx_reader import open_docx
from a11yfix.rules.document_title import DocumentTitleRule

pytestmark = pytest.mark.skipif(
    shutil.which("officecli") is None, reason="officecli not installed"
)


def _core_title(path) -> str | None:
    z = zipfile.ZipFile(path)
    if "docProps/core.xml" not in z.namelist():
        return None
    root = ET.fromstring(z.read("docProps/core.xml"))
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "title":
            return el.text
    return None


def test_document_title_fix_persists_to_disk(docx_no_title, tmp_path):
    """The whole point of the fix: after a deterministic run the title is
    actually written into docProps/core.xml — not merely accepted by officecli."""
    work = tmp_path / "Quarterly Safety Review.docx"
    shutil.copy2(docx_no_title, work)

    doc = open_docx(work)
    findings = [f for f in DocumentTitleRule().detect(doc) if f.rule_id == "document-title-missing"]
    assert findings, "fixture should be missing a title"

    result = apply_deterministic_fixes(findings, doc)

    assert any(
        a.rule_id == "document-title-missing" for a in result.applied
    ), f"title fix did not apply; deferred={[d.rule_id for d in result.deferred]}"
    title = _core_title(work)
    assert title and title.strip(), f"title not persisted to core.xml: {title!r}"


def test_noop_run_leaves_file_byte_identical(docx_with_title, tmp_path):
    """A run with no applicable deterministic fix must not mutate or version-stamp
    the file (no officecli round-trip residue)."""
    work = tmp_path / "already_titled.docx"
    shutil.copy2(docx_with_title, work)
    before = work.read_bytes()

    doc = open_docx(work)
    # document_title won't fire (title present); table-header rule has nothing to do.
    findings = list(DocumentTitleRule().detect(doc))
    apply_deterministic_fixes(findings, doc)

    assert work.read_bytes() == before, "no-op run mutated the file"
