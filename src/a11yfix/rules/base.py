"""Rule base class, registry, and helper protocols.

Every rule lives in its own module under src/a11yfix/rules/ and registers itself
via the @register_rule decorator. The CLI walks REGISTRY filtered by document
format.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from a11yfix.manifest import FileFormat, Finding, Severity

# ----- Officecli operation primitives -----------------------------------------------------------


@dataclass
class OfficecliOp:
    """A single officecli operation. Maps to one entry in a `officecli batch` JSON array.

    See https://github.com/iOfficeAI/OfficeCli/wiki/command-batch
    """

    verb: str  # "set" | "add" | "remove" | "swap" | "move" | "raw-set"
    path: str  # e.g. "/sld[3]/pic[1]" or "/body/p[2]/r[1]"
    props: dict[str, str] = field(default_factory=dict)
    # for raw-set:
    part: str | None = None
    xml: str | None = None

    def to_batch_entry(self) -> dict[str, object]:
        entry: dict[str, object] = {"command": self.verb, "path": self.path}
        if self.props:
            entry["props"] = self.props
        if self.part is not None:
            entry["part"] = self.part
        if self.xml is not None:
            entry["xml"] = self.xml
        return entry


@dataclass
class SingleShotFix:
    """Stage-3 single-shot AI fix descriptor."""

    kind: str  # "alt-text" | "link-text" | "slide-title"
    finding: Finding
    context: dict[str, object] = field(default_factory=dict)


# ----- Document handle (passed to detect()) -----------------------------------------------------


class DocumentHandle(Protocol):
    """Minimal interface every reader provides."""

    file_format: FileFormat
    path: str

    def root_xml(self) -> object:  # lxml Element
        ...


# ----- Rule protocol ----------------------------------------------------------------------------


@dataclass
class RuleMeta:
    rule_id: str
    severity: Severity
    formats: set[FileFormat]
    wcag_sc: list[str]
    plain_impact: str


class Rule(Protocol):
    meta: RuleMeta

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]: ...

    def fix_deterministic(self, finding: Finding, doc: DocumentHandle) -> list[OfficecliOp] | None:
        """Return officecli ops, or None if this rule defers."""
        ...

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        """Return a single-shot AI fix descriptor, or None if this rule defers."""
        ...


# ----- Registry ---------------------------------------------------------------------------------

REGISTRY: dict[str, Rule] = {}

T = TypeVar("T", bound=Rule)


def register_rule(rule: T) -> T:
    """Decorator/function: register a rule instance into the global registry."""
    if rule.meta.rule_id in REGISTRY:
        raise RuntimeError(f"duplicate rule_id: {rule.meta.rule_id}")
    REGISTRY[rule.meta.rule_id] = rule
    return rule


def rules_for(fmt: FileFormat) -> list[Rule]:
    return [r for r in REGISTRY.values() if fmt in r.meta.formats]


# ----- BaseRule mixin (default no-op fixers) ----------------------------------------------------


class BaseRule:
    """Mixin providing default no-op implementations for fix methods."""

    meta: RuleMeta

    def fix_deterministic(self, finding: Finding, doc: DocumentHandle) -> list[OfficecliOp] | None:
        return None

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        return None
