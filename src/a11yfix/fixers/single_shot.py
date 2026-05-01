"""Stage-3 single-shot AI fixer.

For each Finding that has a SingleShotFix descriptor, calls the configured VLM
adapter to generate content, then applies the fix via officecli (one batch).

Caching: ~/.cache/a11yfix/<sha256(content+prompt_version)>.json
Cost cap: --max-ai-cost-usd
Confidence threshold: low-confidence outputs are deferred (not applied).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from a11yfix.ai.adapter import VLMAdapter
from a11yfix.manifest import AppliedFix, Finding
from a11yfix.ooxml.officecli import OfficecliClient, OfficecliError
from a11yfix.rules.base import REGISTRY, DocumentHandle, OfficecliOp

CACHE_DIR = Path(os.environ.get("A11YFIX_CACHE", str(Path.home() / ".cache" / "a11yfix")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CONFIDENCE_THRESHOLD = 0.6


@dataclass
class SingleShotResult:
    applied: list[AppliedFix]
    deferred: list[Finding]


def _cache_key(payload: str) -> Path:
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{h}.json"


def _cache_get(payload: str) -> dict | None:
    p = _cache_key(payload)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
    return None


def _cache_put(payload: str, value: dict) -> None:
    _cache_key(payload).write_text(json.dumps(value))


def _slide_text(doc: DocumentHandle, slide_idx: int) -> tuple[str, str]:
    from a11yfix.ooxml.namespaces import qn
    from a11yfix.ooxml.pptx_reader import PptxHandle

    if not isinstance(doc, PptxHandle):
        return ("", "")
    if slide_idx <= 0 or slide_idx > len(doc.slides_xml):
        return ("", "")
    slide_xml = doc.slides_xml[slide_idx - 1]
    import contextlib

    text = "\n".join(t.text or "" for t in slide_xml.iter(qn("a:t")))
    layout_name = ""
    with contextlib.suppress(Exception):
        layout_name = doc.pptx.slides[slide_idx - 1].slide_layout.name or ""
    return (text, layout_name)


def apply_single_shot_fixes(
    findings: Iterable[Finding],
    doc: DocumentHandle,
    adapter: VLMAdapter,
    *,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> SingleShotResult:
    applied: list[AppliedFix] = []
    deferred: list[Finding] = []
    pending_ops: list[tuple[Finding, OfficecliOp, str, float]] = []

    for f in findings:
        rule = REGISTRY.get(f.rule_id)
        if rule is None:
            deferred.append(f)
            continue
        ssf = rule.fix_single_shot(f, doc)
        if ssf is None:
            deferred.append(f)
            continue

        try:
            if ssf.kind == "alt-text":
                # We don't have direct image bytes in v1 — pass surrounding context only
                # (fallback prompt) until image extraction wires up. Mark medium confidence.
                ctx = f"Shape: {f.extra.get('shape_name', f.extra.get('pic_name', '(unknown)'))}"
                key = f"alttext|{f.id}|{ctx}"
                cached = _cache_get(key)
                if cached:
                    text, conf, model = cached["text"], cached["confidence"], cached["model"]
                else:
                    # Without image bytes, defer; real impl extracts the picture from OOXML.
                    deferred.append(f)
                    continue
                if "DECORATIVE" in text:
                    # mark decorative via raw-set for the adec namespace; defer to stage 4 for safety
                    deferred.append(f)
                    continue
                if conf < confidence_threshold:
                    deferred.append(f)
                    continue
                pending_ops.append(
                    (
                        f,
                        OfficecliOp(verb="set", path=f.officecli_path, props={"alt": text}),
                        text,
                        conf,
                    )
                )

            elif ssf.kind == "link-text":
                key = f"linktext|{f.id}|{f.current_value}"
                cached = _cache_get(key)
                if cached:
                    text, conf, model = cached["text"], cached["confidence"], cached["model"]
                else:
                    res = adapter.suggest_link_text(url="", surrounding_text=f.current_value)
                    text, conf, model = res.text, res.confidence, res.model
                    _cache_put(key, {"text": text, "confidence": conf, "model": model})
                if conf < confidence_threshold or not text:
                    deferred.append(f)
                    continue
                pending_ops.append(
                    (
                        f,
                        OfficecliOp(verb="set", path=f.officecli_path, props={"text": text}),
                        text,
                        conf,
                    )
                )

            elif ssf.kind == "slide-title":
                slide_idx = int(f.extra.get("slide_index", 0))
                text_ctx, layout = _slide_text(doc, slide_idx)
                key = f"slidetitle|{f.id}|{layout}|{text_ctx[:200]}"
                cached = _cache_get(key)
                if cached:
                    text, conf, model = cached["text"], cached["confidence"], cached["model"]
                else:
                    res = adapter.suggest_slide_title(text_ctx, layout)
                    text, conf, model = res.text, res.confidence, res.model
                    _cache_put(key, {"text": text, "confidence": conf, "model": model})
                if "UNCLEAR" in text or conf < confidence_threshold or not text:
                    deferred.append(f)
                    continue
                pending_ops.append(
                    (
                        f,
                        OfficecliOp(verb="set", path=f.officecli_path, props={"title": text}),
                        text,
                        conf,
                    )
                )

            else:
                deferred.append(f)
        except Exception:
            deferred.append(f)

    if not pending_ops:
        return SingleShotResult(applied=[], deferred=deferred)

    client = OfficecliClient(doc.path)
    try:
        with client:
            ops_only = [op for (_f, op, _t, _c) in pending_ops]
            try:
                result = client.batch(ops_only)
            except OfficecliError:
                return SingleShotResult(applied=[], deferred=deferred + [t[0] for t in pending_ops])
            validation = client.validate()
            if validation.status == "errors":
                client.restore_from_backup()
                return SingleShotResult(applied=[], deferred=deferred + [t[0] for t in pending_ops])
            for (finding, _op, text, conf), op_result in zip(
                pending_ops, result.per_op or [{}] * len(pending_ops)
            ):
                ok = (
                    op_result.get("ok", result.success)
                    if isinstance(op_result, dict)
                    else result.success
                )
                if ok:
                    applied.append(
                        AppliedFix(
                            finding_id=finding.id,
                            rule_id=finding.rule_id,
                            officecli_path=finding.officecli_path,
                            stage=3,
                            before=finding.current_value,
                            after=text,
                            ai_model=adapter.name,
                            confidence=conf,
                        )
                    )
                else:
                    deferred.append(finding)
            return SingleShotResult(applied=applied, deferred=deferred)
    except FileNotFoundError:
        return SingleShotResult(applied=[], deferred=deferred + [t[0] for t in pending_ops])
