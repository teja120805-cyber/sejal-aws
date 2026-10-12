"""Waste-detection rule contract.

Each rule is a small class: metadata (id, severity, description), default thresholds that users can
override per account, `detect()` and `estimated_monthly_savings()`. Adding a rule = adding one class
to the registry; the engine, API and UI pick it up automatically.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from finops.domain import AccountSettings, Finding, Resource, ResourceType, Severity, to_money
from finops.pricing import PriceCatalog


@dataclass
class RuleContext:
    account_id: str
    now: datetime
    catalog: PriceCatalog
    settings: AccountSettings
    # Every resource of the account by resource_id, for rules that look across resources
    # (e.g. a stopped instance's attached volumes).
    resources: dict[str, Resource] = field(default_factory=dict)

    def volumes_of(self, instance: Resource) -> list[Resource]:
        ids = set(instance.attributes.get("volume_ids") or [])
        return [
            r
            for r in self.resources.values()
            if r.type == ResourceType.EBS_VOLUME
            and (
                r.resource_id in ids
                or r.attributes.get("attached_instance_id") == instance.resource_id
            )
        ]


class Rule(ABC):
    id: ClassVar[str]
    title: ClassVar[str]
    severity: ClassVar[Severity]
    resource_type: ClassVar[ResourceType]
    description: ClassVar[str]
    default_thresholds: ClassVar[dict[str, float]] = {}

    def thresholds(self, ctx: RuleContext) -> dict[str, float]:
        return self.default_thresholds | ctx.settings.rule_thresholds.get(self.id, {})

    @abstractmethod
    def detect(self, resource: Resource, ctx: RuleContext) -> bool: ...

    @abstractmethod
    def estimated_monthly_savings(self, resource: Resource, ctx: RuleContext) -> Decimal: ...

    @abstractmethod
    def explain(self, resource: Resource, ctx: RuleContext) -> tuple[str, str]:
        """(detail, recommendation) shown to the user."""

    def current_cost(self, resource: Resource, ctx: RuleContext) -> Decimal | None:
        return resource.est_monthly_cost_usd

    def evaluate(self, resources: list[Resource], ctx: RuleContext) -> list[Finding]:
        findings = []
        for r in resources:
            if r.type != self.resource_type or not self.detect(r, ctx):
                continue
            detail, recommendation = self.explain(r, ctx)
            current = self.current_cost(r, ctx)
            findings.append(
                Finding(
                    account_id=ctx.account_id,
                    rule_id=self.id,
                    resource_id=r.resource_id,
                    resource_type=r.type,
                    resource_name=r.name,
                    region=r.region,
                    severity=self.severity,
                    title=self.title,
                    detail=detail,
                    recommendation=recommendation,
                    current_monthly_cost_usd=to_money(current) if current is not None else None,
                    est_monthly_savings_usd=to_money(self.estimated_monthly_savings(r, ctx)),
                    first_seen=ctx.now,
                    last_seen=ctx.now,
                )
            )
        return findings

    @classmethod
    def metadata(cls) -> dict[str, object]:
        return {
            "id": cls.id,
            "title": cls.title,
            "severity": cls.severity.value,
            "resource_type": cls.resource_type.value,
            "description": cls.description,
            "default_thresholds": dict(cls.default_thresholds),
        }


def age_days(when: datetime | str | None, now: datetime) -> float | None:
    if when is None:
        return None
    if isinstance(when, str):
        when = datetime.fromisoformat(when)
    return (now - when).total_seconds() / 86400
