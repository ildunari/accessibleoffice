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
from a11yfix.cost_meter import CostMeter
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


def _cache_put(payload: str, value: dict, *, min_confidence: float = 0.7) -> None:
    """Only persist responses we'd actually use — never cache low-confidence junk."""
    if float(value.get("confidence", 0.0)) < min_confidence:
        return
    if not str(value.get("text", "")).strip():
        return
    if "UNCLEAR" in str(value.get("text", "")):
        return
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
    max_cost_total_usd: float | None = None,
) -> SingleShotResult:
    """Run stage-3 AI fixes.

    If `max_cost_total_usd` is set, the batch's CostMeter is consulted before
    every adapter call; once the cap is reached, remaining findings defer
    instead of calling the model. Per-call cost is recorded by the adapter.
    """
    applied: list[AppliedFix] = []
    deferred: list[Finding] = []
    pending_ops: list[tuple[Finding, OfficecliOp, str, float]] = []

    meter = CostMeter.from_env()
    cap_hit_logged = False

    for f in findings:
        rule = REGISTRY.get(f.rule_id)
        if rule is None:
            deferred.append(f)
            continue
        ssf = rule.fix_single_shot(f, doc)
        if ssf is None:
            deferred.append(f)
            continue

        # Cost-cap gate: if the batch has already spent its budget, defer.
        if max_cost_total_usd is not None and meter.would_exceed(max_cost_total_usd):
            if not cap_hit_logged:
                import sys as _sys

                print(
                    f"[stage3] cost cap ${max_cost_total_usd:.2f} reached "
                    f"(spent ${meter.total():.4f}); deferring remaining findings",
                    file=_sys.stderr,
                )
                cap_hit_logged = True
            f.why_human_needed = "stage-3 deferred: batch cost cap reached"
            deferred.append(f)
            continue

        try:
            if ssf.kind == "alt-text":
                from a11yfix.ooxml.image_extract import extract_image_for_finding

                ctx = f"Shape: {f.extra.get('shape_name', f.extra.get('pic_name', '(unknown)'))}"
                extracted = extract_image_for_finding(doc, f)
                if extracted is None:
                    # No image bytes recoverable — defer to stage 4.
                    deferred.append(f)
                    continue
                img_bytes, mime = extracted
                key = f"alttext|{hashlib.sha256(img_bytes).hexdigest()}|{ctx}"
                cached = _cache_get(key)
                if cached:
                    text, conf, model = cached["text"], cached["confidence"], cached["model"]
                else:
                    res = adapter.describe_image(
                        img_bytes, max_chars=125, context=ctx
                    )
                    text, conf, model = res.text, res.confidence, res.model
                    _cache_put(key, {"text": text, "confidence": conf, "model": model})
                if "DECORATIVE" in text or "UNCLEAR" in text:
                    deferred.append(f)
                    continue
                if conf < confidence_threshold or not text:
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
                url = str(f.extra.get("url", ""))
                surrounding = str(
                    f.extra.get("paragraph_text") or f.extra.get("shape_text") or f.current_value
                )
                key = f"linktext|{f.id}|{url}|{surrounding[:200]}"
                cached = _cache_get(key)
                if cached:
                    text, conf, model = cached["text"], cached["confidence"], cached["model"]
                else:
                    res = adapter.suggest_link_text(url=url, surrounding_text=surrounding)
                    text, conf, model = res.text, res.confidence, res.model
                    _cache_put(key, {"text": text, "confidence": conf, "model": model})
                if conf < confidence_threshold or not text or "UNCLEAR" in text:
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
        except Exception as exc:
            import sys as _sys

            print(
                f"[stage3] {f.id} ({f.rule_id}) deferred: {type(exc).__name__}: {exc}",
                file=_sys.stderr,
            )
            deferred.append(f)

    if not pending_ops:
        return SingleShotResult(applied=[], deferred=deferred)

    client = OfficecliClient(doc.path)
    try:
        with client:
            ops_only = [op for (_f, op, _t, _c) in pending_ops]
            try:
                result = client.batch(ops_only)
            except OfficecliError as exc:
                import sys as _sys

                print(f"[stage3] officecli batch failed: {exc}", file=_sys.stderr)
                return SingleShotResult(applied=[], deferred=deferred + [t[0] for t in pending_ops])
            validation = client.validate()
            if validation.status == "errors":
                import sys as _sys

                print(
                    f"[stage3] validate errors after batch — restoring: {validation.errors}",
                    file=_sys.stderr,
                )
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
