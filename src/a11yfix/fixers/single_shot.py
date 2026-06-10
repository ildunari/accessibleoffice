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
from itertools import zip_longest
from pathlib import Path

from a11yfix.ai.adapter import VLMAdapter
from a11yfix.cost_meter import CostMeter
from a11yfix.manifest import AppliedFix, Finding
from a11yfix.ooxml.officecli import OfficecliClient, OfficecliError
from a11yfix.rules.base import REGISTRY, DocumentHandle, OfficecliOp

CACHE_DIR = Path(os.environ.get("A11YFIX_CACHE", str(Path.home() / ".cache" / "a11yfix")))

CONFIDENCE_THRESHOLD = 0.6


@dataclass
class SingleShotResult:
    applied: list[AppliedFix]
    deferred: list[Finding]


def _defer(finding: Finding, deferred: list[Finding], reason: str) -> None:
    # Only fill in an empty reason — never clobber one set by detection or by
    # an earlier deferral (Finding objects are shared with the manifest).
    if not finding.why_human_needed:
        finding.why_human_needed = reason
    deferred.append(finding)


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
    # Created lazily so importing this module doesn't touch the filesystem.
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_key(payload).write_text(json.dumps(value))


def _record_usage(res) -> None:
    """Record one fresh adapter call into the batch cost ledger.

    Centralized here (not in adapters) so every backend meters identically
    and cache hits never record. Backend-reported USD wins over estimates.
    """
    usage = getattr(res, "usage", None)
    if usage is None:
        return
    meter = CostMeter.from_env()
    if usage.cost_usd is not None:
        meter.record_usd(model=res.model, usd=usage.cost_usd)
    else:
        meter.record(
            model=res.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )


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
    instead of calling the model. Per-call cost is recorded centrally from
    each result's `usage`.
    """
    applied: list[AppliedFix] = []
    deferred: list[Finding] = []
    pending_ops: list[tuple[Finding, OfficecliOp, str, float]] = []

    meter = CostMeter.from_env()
    cap_hit_logged = False

    for f in findings:
        rule = REGISTRY.get(f.rule_id)
        if rule is None:
            _defer(f, deferred, "stage-3 deferred: no registered rule")
            continue
        ssf = rule.fix_single_shot(f, doc)
        if ssf is None:
            _defer(f, deferred, "stage-3 deferred: no single-shot fix available")
            continue

        # Cost-cap gate: cap <= 0 means no model calls; otherwise defer once spent.
        if max_cost_total_usd is not None and (
            max_cost_total_usd <= 0 or meter.would_exceed(max_cost_total_usd)
        ):
            if not cap_hit_logged:
                import sys as _sys

                print(
                    f"[stage3] cost cap ${max_cost_total_usd:.2f} reached "
                    f"(spent ${meter.total():.4f}); deferring remaining findings",
                    file=_sys.stderr,
                )
                cap_hit_logged = True
            _defer(f, deferred, "stage-3 deferred: batch cost cap reached")
            continue

        try:
            if ssf.kind == "alt-text":
                from a11yfix.ooxml.image_extract import extract_image_for_finding

                ctx = f"Shape: {f.extra.get('shape_name', f.extra.get('pic_name', '(unknown)'))}"
                extracted = extract_image_for_finding(doc, f)
                if extracted is None:
                    _defer(f, deferred, "stage-3 deferred: image bytes not recoverable")
                    continue
                img_bytes, _mime = extracted
                key = f"alttext|{hashlib.sha256(img_bytes).hexdigest()}|{ctx}"
                cached = _cache_get(key)
                if cached:
                    text, conf, model = cached["text"], cached["confidence"], cached["model"]
                else:
                    res = adapter.describe_image(
                        img_bytes, max_chars=125, context=ctx
                    )
                    _record_usage(res)
                    text, conf, model = res.text, res.confidence, res.model
                    _cache_put(key, {"text": text, "confidence": conf, "model": model})
                if "DECORATIVE" in text:
                    _defer(f, deferred, "stage-3 deferred: model judged image decorative")
                    continue
                if "UNCLEAR" in text:
                    _defer(f, deferred, "stage-3 deferred: model returned UNCLEAR")
                    continue
                if not text:
                    _defer(f, deferred, "stage-3 deferred: model returned empty text")
                    continue
                if conf < confidence_threshold:
                    _defer(
                        f,
                        deferred,
                        f"stage-3 deferred: low confidence ({conf:.2f} < {confidence_threshold:.2f})",
                    )
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
                # Content-based key (no finding id): the same link in another
                # paragraph or a later re-run still hits the cache. Hash the
                # full surrounding text so the key covers exactly what the
                # model sees — a truncated key would collide for contexts
                # that only diverge later.
                surrounding_h = hashlib.sha256(surrounding.encode("utf-8")).hexdigest()
                key = f"linktext|{url}|{surrounding_h}"
                cached = _cache_get(key)
                if cached:
                    text, conf, model = cached["text"], cached["confidence"], cached["model"]
                else:
                    res = adapter.suggest_link_text(url=url, surrounding_text=surrounding)
                    _record_usage(res)
                    text, conf, model = res.text, res.confidence, res.model
                    _cache_put(key, {"text": text, "confidence": conf, "model": model})
                if "UNCLEAR" in text:
                    _defer(f, deferred, "stage-3 deferred: model returned UNCLEAR")
                    continue
                if not text:
                    _defer(f, deferred, "stage-3 deferred: model returned empty text")
                    continue
                if conf < confidence_threshold:
                    _defer(
                        f,
                        deferred,
                        f"stage-3 deferred: low confidence ({conf:.2f} < {confidence_threshold:.2f})",
                    )
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
                # Content-based key (no finding id) so re-runs hit the cache.
                # Hash the full slide text: the model gets all of it, so a
                # truncated key would hand one slide's cached title to a
                # different slide that merely shares the same opening text
                # (the cache is persistent, so collisions cross runs/decks).
                text_h = hashlib.sha256(text_ctx.encode("utf-8")).hexdigest()
                key = f"slidetitle|{layout}|{text_h}"
                cached = _cache_get(key)
                if cached:
                    text, conf, model = cached["text"], cached["confidence"], cached["model"]
                else:
                    res = adapter.suggest_slide_title(text_ctx, layout)
                    _record_usage(res)
                    text, conf, model = res.text, res.confidence, res.model
                    _cache_put(key, {"text": text, "confidence": conf, "model": model})
                if "UNCLEAR" in text:
                    _defer(f, deferred, "stage-3 deferred: model returned UNCLEAR")
                    continue
                if not text:
                    _defer(f, deferred, "stage-3 deferred: model returned empty text")
                    continue
                if conf < confidence_threshold:
                    _defer(
                        f,
                        deferred,
                        f"stage-3 deferred: low confidence ({conf:.2f} < {confidence_threshold:.2f})",
                    )
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
                _defer(f, deferred, f"stage-3 deferred: unsupported fix kind {ssf.kind}")
        except Exception as exc:
            import sys as _sys

            print(
                f"[stage3] {f.id} ({f.rule_id}) deferred: {type(exc).__name__}: {exc}",
                file=_sys.stderr,
            )
            _defer(f, deferred, f"stage-3 deferred: {type(exc).__name__}: {exc}")

    if not pending_ops:
        return SingleShotResult(applied=[], deferred=deferred)

    # Distinct backup name: stage 2's ".bak" is the pristine pre-pipeline
    # copy (recorded in the manifest) and must survive stage 3. Restoring
    # here intentionally reverts to the post-stage-2 state, not the original.
    client = OfficecliClient(doc.path, backup_suffix=".stage3.bak")
    try:
        with client:
            ops_only = [op for (_f, op, _t, _c) in pending_ops]
            try:
                result = client.batch(ops_only)
            except OfficecliError as exc:
                import sys as _sys

                print(f"[stage3] officecli batch failed: {exc}", file=_sys.stderr)
                for finding, _op, _text, _conf in pending_ops:
                    _defer(finding, deferred, f"stage-3 deferred: officecli batch failed: {exc}")
                return SingleShotResult(applied=[], deferred=deferred)
            validation = client.validate()
            if validation.status == "errors":
                import sys as _sys

                print(
                    f"[stage3] validate errors after batch — restoring: {validation.errors}",
                    file=_sys.stderr,
                )
                client.restore_from_backup()
                for finding, _op, _text, _conf in pending_ops:
                    _defer(finding, deferred, "stage-3 deferred: validation failed after write")
                return SingleShotResult(applied=[], deferred=deferred)
            for pending, op_result in zip_longest(pending_ops, result.per_op or []):
                if pending is None:
                    continue
                finding, _op, text, conf = pending
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
                    _defer(finding, deferred, "stage-3 deferred: officecli did not apply operation")
            # If nothing actually applied, the officecli open/save round-trip
            # still rewrote and version-stamped the package. Restore the
            # post-stage-2 backup so a no-op stage 3 leaves the file's bytes
            # untouched (mirrors the stage-2 guard in deterministic.py).
            if not applied:
                client.restore_from_backup()
            return SingleShotResult(applied=applied, deferred=deferred)
    except FileNotFoundError:
        for finding, _op, _text, _conf in pending_ops:
            _defer(finding, deferred, "stage-3 deferred: officecli not found")
        return SingleShotResult(applied=[], deferred=deferred)
