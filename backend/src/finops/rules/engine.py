"""Runs all rules, removes double counting, and syncs findings with what is stored."""

from datetime import datetime
from decimal import Decimal

from finops.domain import AccountSettings, Finding, FindingStatus, Resource
from finops.pricing import PriceCatalog, default_catalog
from finops.repositories import Repositories
from finops.rules.base import RuleContext
from finops.rules.definitions import ALL_RULES, Ec2StoppedWithEbsRule


def run_rules(
    account_id: str,
    resources: list[Resource],
    settings: AccountSettings,
    now: datetime,
    catalog: PriceCatalog | None = None,
) -> list[Finding]:
    ctx = RuleContext(
        account_id=account_id,
        now=now,
        catalog=catalog or default_catalog(),
        settings=settings,
        resources={r.resource_id: r for r in resources},
    )
    findings = [f for rule in ALL_RULES for f in rule.evaluate(resources, ctx)]
    return _drop_superseded(findings, ctx)


def _drop_superseded(findings: list[Finding], ctx: RuleContext) -> list[Finding]:
    """A volume recommended for deletion shouldn't also be counted for a gp2->gp3 migration;
    otherwise "total potential savings" would double count the same dollars."""
    deleted_volumes = {f.resource_id for f in findings if f.rule_id == "ebs-unattached"}
    for f in findings:
        if f.rule_id == Ec2StoppedWithEbsRule.id:
            instance = ctx.resources[f.resource_id]
            deleted_volumes |= {v.resource_id for v in ctx.volumes_of(instance)}
    return [
        f
        for f in findings
        if not (f.rule_id == "ebs-gp2-to-gp3" and f.resource_id in deleted_volumes)
    ]


def sync_findings(
    repos: Repositories, account_id: str, current: list[Finding], now: datetime
) -> list[Finding]:
    """Merge a fresh rule run into stored findings:
    - still detected: keep first_seen and a user's "dismissed" status, refresh everything else
    - newly detected or detected again after being resolved: open
    - no longer detected: resolved (kept for history)
    """
    stored = {f.id: f for f in repos.findings.list(account_id)}
    out: list[Finding] = []
    for f in current:
        old = stored.pop(f.id, None)
        if old is not None:
            f.first_seen = old.first_seen
            if old.status == FindingStatus.DISMISSED:
                f.status = FindingStatus.DISMISSED
        out.append(f)
    for old in stored.values():
        if old.status == FindingStatus.OPEN:
            old.status = FindingStatus.RESOLVED
            old.last_seen = now
        out.append(old)
    repos.findings.put_many(out)
    return out


def refresh_findings(
    repos: Repositories, account_id: str, now: datetime, catalog: PriceCatalog | None = None
) -> list[Finding]:
    findings = run_rules(
        account_id,
        repos.resources.list(account_id),
        repos.settings.get(account_id),
        now,
        catalog,
    )
    return sync_findings(repos, account_id, findings, now)


def total_savings(findings: list[Finding]) -> Decimal:
    return sum(
        (f.est_monthly_savings_usd for f in findings if f.status == FindingStatus.OPEN),
        Decimal(0),
    )
