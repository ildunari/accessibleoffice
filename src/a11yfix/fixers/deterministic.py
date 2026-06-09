"""Stage-2 deterministic fixer: walks rules, collects ops, batches via officecli.

Auto-revert on validate failure. Idempotent: skips findings already addressed.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import zip_longest

from a11yfix.manifest import AppliedFix, Finding
from a11yfix.ooxml.officecli import OfficecliClient, OfficecliError
from a11yfix.rules.base import (
    REGISTRY,
    DocumentHandle,
    OfficecliOp,
)


@dataclass
class DeterministicResult:
    applied: list[AppliedFix]
    deferred: list[Finding]
    validation_status: str
    validation_errors: list[dict[str, object]]
    backup_path: str | None


def apply_deterministic_fixes(
    findings: list[Finding],
    doc: DocumentHandle,
    *,
    default_lang: str | None = None,
) -> DeterministicResult:
    applied: list[AppliedFix] = []
    deferred: list[Finding] = []
    pending_ops: list[tuple[Finding, OfficecliOp]] = []

    for f in findings:
        rule = REGISTRY.get(f.rule_id)
        if rule is None:
            deferred.append(f)
            continue
        # Special handling for document-language-missing with --default-lang
        if f.rule_id == "document-language-missing" and default_lang:
            pending_ops.append(
                (
                    f,
                    OfficecliOp(verb="set", path=f.officecli_path, props={"value": default_lang}),
                )
            )
            continue
        ops = rule.fix_deterministic(f, doc)
        if ops is None:
            deferred.append(f)
            continue
        for op in ops:
            pending_ops.append((f, op))

    if not pending_ops:
        return DeterministicResult(
            applied=[],
            deferred=deferred,
            validation_status="skipped",
            validation_errors=[],
            backup_path=None,
        )

    client = OfficecliClient(doc.path)
    try:
        with client:
            backup_path = str(client.backup_path) if client.backup_path else None
            ops_only = [op for (_f, op) in pending_ops]
            try:
                result = client.batch(ops_only)
            except OfficecliError:
                # Officecli not available or hard failure → defer everything.
                # `findings` REPLACES the deferred list here (it equals
                # deferred ∪ pending, each finding exactly once).
                return DeterministicResult(
                    applied=[],
                    deferred=findings,
                    validation_status="skipped",
                    validation_errors=[{"reason": "officecli unavailable"}],
                    backup_path=backup_path,
                )

            # Validate; on failure restore.
            validation = client.validate()
            if validation.status == "errors":
                client.restore_from_backup()
                return DeterministicResult(
                    applied=[],
                    deferred=findings,
                    validation_status="errors",
                    validation_errors=validation.errors,
                    backup_path=backup_path,
                )

            for pending, op_result in zip_longest(pending_ops, result.per_op or []):
                if pending is None:
                    continue
                finding, op = pending
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
                            stage=2,
                            before=finding.current_value,
                            after=str(op.props),
                        )
                    )
                else:
                    deferred.append(finding)

            return DeterministicResult(
                applied=applied,
                deferred=deferred,
                validation_status=validation.status,
                validation_errors=validation.errors,
                backup_path=backup_path,
            )
    except FileNotFoundError:
        # officecli not installed
        return DeterministicResult(
            applied=[],
            deferred=findings,
            validation_status="skipped",
            validation_errors=[{"reason": "officecli not installed"}],
            backup_path=None,
        )
