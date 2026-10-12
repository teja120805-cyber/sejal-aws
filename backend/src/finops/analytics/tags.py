"""Tag compliance: which resources are missing the account's required tags."""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from finops.domain import Resource


@dataclass
class TagComplianceReport:
    required_tags: list[str]
    total_resources: int
    compliant_resources: int
    compliance_pct: float
    missing_by_tag: dict[str, int]
    noncompliant: list[dict[str, object]] = field(default_factory=list)
    untagged_monthly_cost_usd: Decimal = Decimal(0)


def tag_compliance(resources: list[Resource], required: list[str]) -> TagComplianceReport:
    missing_counter: Counter[str] = Counter()
    noncompliant: list[dict[str, object]] = []
    untagged_cost = Decimal(0)
    for r in resources:
        present = {k for k, v in r.tags.items() if v.strip()}
        missing = [t for t in required if t not in present]
        if missing:
            missing_counter.update(missing)
            untagged_cost += r.est_monthly_cost_usd
            noncompliant.append(
                {
                    "resource_id": r.resource_id,
                    "name": r.name,
                    "type": r.type.value,
                    "region": r.region,
                    "missing_tags": missing,
                    "tags": r.tags,
                    "est_monthly_cost_usd": float(r.est_monthly_cost_usd),
                }
            )
    total = len(resources)
    compliant = total - len(noncompliant)
    noncompliant.sort(key=lambda row: float(str(row["est_monthly_cost_usd"])), reverse=True)
    return TagComplianceReport(
        required_tags=list(required),
        total_resources=total,
        compliant_resources=compliant,
        compliance_pct=round(compliant / total * 100, 1) if total else 100.0,
        missing_by_tag={t: missing_counter.get(t, 0) for t in required},
        noncompliant=noncompliant,
        untagged_monthly_cost_usd=untagged_cost,
    )
