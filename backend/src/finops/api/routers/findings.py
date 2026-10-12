from collections import defaultdict
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from finops.api.auth import CurrentUser
from finops.api.deps import AccountDep, ReposDep, SettingsDep
from finops.aws.session import AccountSession
from finops.domain import Finding, FindingStatus, Severity
from finops.remediation import RemediationError, RemediationPreview, execute, mark_remediated
from finops.remediation import preview as remediation_preview
from finops.repositories import NotFoundError
from finops.rules import ALL_RULES, refresh_findings, total_savings

router = APIRouter(prefix="/accounts/{account_id}", tags=["findings"])

SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


class StatusUpdate(BaseModel):
    status: Literal["open", "dismissed"]


class RemediationRequest(BaseModel):
    confirm: bool
    confirm_resource_id: str  # user must type the resource ID to confirm


@router.get("/findings")
def list_findings(
    account: AccountDep,
    repos: ReposDep,
    _: CurrentUser,
    status: FindingStatus | None = FindingStatus.OPEN,
    severity: Severity | None = None,
    rule_id: str | None = None,
) -> dict[str, object]:
    findings = repos.findings.list(account.id, status=status)
    if severity:
        findings = [f for f in findings if f.severity == severity]
    if rule_id:
        findings = [f for f in findings if f.rule_id == rule_id]
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.est_monthly_savings_usd))

    by_rule: dict[str, dict[str, object]] = {}
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for f in findings:
        sums[f.rule_id] += f.est_monthly_savings_usd
        by_rule.setdefault(f.rule_id, {"rule_id": f.rule_id, "title": f.title, "count": 0})
        by_rule[f.rule_id]["count"] += 1  # type: ignore[operator]
    for rule_id_, row in by_rule.items():
        row["savings_usd"] = float(sums[rule_id_])
    return {
        "items": findings,
        "total": len(findings),
        "total_monthly_savings_usd": float(
            total_savings(findings) if status in (None, FindingStatus.OPEN) else sum(sums.values())
        ),
        "by_rule": sorted(by_rule.values(), key=lambda r: -float(str(r["savings_usd"]))),
        "by_severity": {s.value: sum(1 for f in findings if f.severity == s) for s in Severity},
    }


@router.patch("/findings/{finding_id:path}")
def update_finding(
    finding_id: str, body: StatusUpdate, account: AccountDep, repos: ReposDep, _: CurrentUser
) -> Finding:
    try:
        return repos.findings.update_status(account.id, finding_id, FindingStatus(body.status))
    except NotFoundError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found") from err


@router.get("/rules")
def account_rules(account: AccountDep, repos: ReposDep, _: CurrentUser) -> list[dict[str, object]]:
    overrides = repos.settings.get(account.id).rule_thresholds
    return [
        rule.metadata() | {"thresholds": dict(rule.default_thresholds) | overrides.get(rule.id, {})}
        for rule in ALL_RULES
    ]


@router.post("/findings/recompute")
def recompute(account: AccountDep, repos: ReposDep, _: CurrentUser) -> dict[str, object]:
    """Re-run the rules on the stored inventory (free: no AWS calls)."""
    from finops.domain import utcnow

    findings = refresh_findings(repos, account.id, utcnow())
    open_ = [f for f in findings if f.status == FindingStatus.OPEN]
    return {"open_findings": len(open_), "total_monthly_savings_usd": float(total_savings(open_))}


def _finding_or_404(repos: ReposDep, account_id: str, finding_id: str) -> Finding:
    finding = repos.findings.get(account_id, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    return finding


@router.get("/remediation/{finding_id:path}")
def get_remediation(
    finding_id: str, account: AccountDep, repos: ReposDep, settings: SettingsDep, _: CurrentUser
) -> RemediationPreview:
    """Exactly what would happen - shown in the confirmation dialog."""
    finding = _finding_or_404(repos, account.id, finding_id)
    return remediation_preview(finding, account, settings.remediation_enabled)


@router.post("/remediation/{finding_id:path}")
def run_remediation(
    finding_id: str,
    body: RemediationRequest,
    account: AccountDep,
    repos: ReposDep,
    settings: SettingsDep,
    _: CurrentUser,
) -> dict[str, object]:
    finding = _finding_or_404(repos, account.id, finding_id)
    plan = remediation_preview(finding, account, settings.remediation_enabled)
    if not plan.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, plan.disabled_reason or "not allowed")
    if not body.confirm or body.confirm_resource_id != finding.resource_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "confirmation does not match resource")

    if settings.demo_mode:
        result = _simulate(repos, account.id, finding, plan)
    else:
        try:
            session = AccountSession.assume(
                account,
                role_arn=account.remediation_role_arn,
                session_name="finops-remediation",
                region=settings.aws_region,
            )
            result = execute(finding, account, session)
        except RemediationError as err:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(err)) from err
    repos.findings.put_many([mark_remediated(finding, result)])
    return {"ok": True, "message": result, "monthly_savings_usd": plan.monthly_savings_usd}


def _simulate(repos: ReposDep, account_id: str, finding: Finding, plan: RemediationPreview) -> str:
    """Demo mode: apply the effect to the in-memory inventory instead of AWS."""
    from finops.domain import utcnow

    resources = repos.resources.list(account_id)
    kept = []
    for r in resources:
        if r.resource_id != finding.resource_id:
            kept.append(r)
        elif plan.action == "stop_instance":
            r.state = "stopped"
            r.attributes["stopped_at"] = utcnow().isoformat()
            r.est_monthly_cost_usd = Decimal(0)
            kept.append(r)
    repos.resources.replace_inventory(account_id, kept)
    return f"(demo) {plan.description}"
