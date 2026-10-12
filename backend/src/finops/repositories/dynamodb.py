"""DynamoDB single-table implementation of the repositories.

Key design (every item belongs to one connected AWS account):

    PK = ACCOUNT#{account_id}
    SK = META                                   Account (also GSI1PK=ACCOUNT for listing)
         COST#{date}#{service}#{region}         DailyCost
         TAGCOST#{tag_key}#{date}#{tag_value}   TagCost
         FORECAST#{yyyy-mm}                     CostForecast
         RES#{type}#{region}#{resource_id}      Resource
         FINDING#{rule_id}#{resource_id}        Finding
         BUDGET                                 Budget
         SETTINGS                               AccountSettings
         ALERT#{created_at}#{id}                Alert
         DEDUP#{key}                            alert de-duplication marker (expires via TTL)
         RUN#{started_at}#{id}                  CollectionRun

ISO dates sort lexicographically, so "costs between two dates" is one Query with BETWEEN.
Datetimes are written in a fixed-width UTC format so string comparison == time comparison.
"""

import datetime as dt
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from pydantic import BaseModel

from finops.domain import (
    Account,
    AccountSettings,
    Alert,
    Budget,
    CollectionRun,
    CostForecast,
    DailyCost,
    Finding,
    FindingStatus,
    Resource,
    ResourceType,
    TagCost,
)
from finops.repositories.base import NotFoundError, Repositories

HIGH = "￿"  # sorts after any character we use in sort keys
ACCOUNTS_GSI = "GSI1"
TTL_ATTRIBUTE = "expires_at"
_KEY_ATTRS = {"PK", "SK", "GSI1PK", "GSI1SK", TTL_ATTRIBUTE}


def format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _pk(account_id: str) -> str:
    return f"ACCOUNT#{account_id}"


def _to_dynamo(value: Any) -> Any:
    """Python -> DynamoDB-safe values (no floats, no datetimes, no enums)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return format_datetime(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """DynamoDB returns every number as Decimal; give plain int/float back to the models
    (Money fields convert to Decimal again during validation)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def _item(model: BaseModel, pk: str, sk: str, **extra: Any) -> dict[str, Any]:
    item = _to_dynamo(model.model_dump(mode="python"))
    item.update(PK=pk, SK=sk, **extra)
    return item


def _model[M: BaseModel](cls: type[M], item: dict[str, Any]) -> M:
    data = {k: v for k, v in item.items() if k not in _KEY_ATTRS}
    return cls.model_validate(_from_dynamo(data))


class _Table:
    """Thin helper around a boto3 Table resource with pagination built in."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def query(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        while True:
            resp = self.table.query(**kwargs)
            yield from resp.get("Items", [])
            if "LastEvaluatedKey" not in resp:
                return
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def query_prefix(self, pk: str, prefix: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        cond = Key("PK").eq(pk) & Key("SK").begins_with(prefix)
        return self.query(KeyConditionExpression=cond, **kwargs)

    def query_between(self, pk: str, low: str, high: str) -> Iterator[dict[str, Any]]:
        cond = Key("PK").eq(pk) & Key("SK").between(low, high)
        return self.query(KeyConditionExpression=cond)

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        return self.table.get_item(Key={"PK": pk, "SK": sk}).get("Item")

    def put(self, item: dict[str, Any], **kwargs: Any) -> None:
        self.table.put_item(Item=item, **kwargs)

    def put_batch(self, items: Iterable[dict[str, Any]]) -> None:
        with self.table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as batch:
            for item in items:
                batch.put_item(Item=item)

    def delete_batch(self, keys: Iterable[tuple[str, str]]) -> None:
        with self.table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as batch:
            for pk, sk in keys:
                batch.delete_item(Key={"PK": pk, "SK": sk})


def _is_conditional_failure(err: ClientError) -> bool:
    return err.response["Error"]["Code"] == "ConditionalCheckFailedException"


class DynamoAccountRepository:
    def __init__(self, t: _Table) -> None:
        self._t = t

    def get(self, account_id: str) -> Account | None:
        item = self._t.get(_pk(account_id), "META")
        return _model(Account, item) if item else None

    def list(self) -> list[Account]:
        items = self._t.query(
            IndexName=ACCOUNTS_GSI, KeyConditionExpression=Key("GSI1PK").eq("ACCOUNT")
        )
        return sorted((_model(Account, i) for i in items), key=lambda a: a.name)

    def put(self, account: Account) -> None:
        self._t.put(_item(account, _pk(account.id), "META", GSI1PK="ACCOUNT", GSI1SK=account.id))

    def delete(self, account_id: str) -> None:
        pk = _pk(account_id)
        keys = [(i["PK"], i["SK"]) for i in self._t.query(KeyConditionExpression=Key("PK").eq(pk))]
        self._t.delete_batch(keys)

    def try_start_refresh(self, account_id: str, now: datetime, cooldown: timedelta) -> bool:
        try:
            self._t.table.update_item(
                Key={"PK": _pk(account_id), "SK": "META"},
                UpdateExpression="SET last_refresh_at = :now",
                # Atomic check-and-set: two simultaneous Refresh clicks can't both pass.
                ConditionExpression=Attr("PK").exists()
                & (
                    Attr("last_refresh_at").not_exists()
                    | Attr("last_refresh_at").attribute_type("NULL")
                    | Attr("last_refresh_at").lte(format_datetime(now - cooldown))
                ),
                ExpressionAttributeValues={":now": format_datetime(now)},
            )
            return True
        except ClientError as err:
            if not _is_conditional_failure(err):
                raise
        if self.get(account_id) is None:
            raise NotFoundError(account_id)
        return False


class DynamoCostRepository:
    def __init__(self, t: _Table) -> None:
        self._t = t

    def put_daily_costs(self, costs: Iterable[DailyCost]) -> None:
        self._t.put_batch(
            _item(c, _pk(c.account_id), f"COST#{c.date.isoformat()}#{c.service}#{c.region}")
            for c in costs
        )

    def query_daily_costs(self, account_id: str, start: dt.date, end: dt.date) -> list[DailyCost]:
        items = self._t.query_between(
            _pk(account_id), f"COST#{start.isoformat()}", f"COST#{end.isoformat()}#{HIGH}"
        )
        return [_model(DailyCost, i) for i in items]

    def put_tag_costs(self, costs: Iterable[TagCost]) -> None:
        self._t.put_batch(
            _item(c, _pk(c.account_id), f"TAGCOST#{c.tag_key}#{c.date.isoformat()}#{c.tag_value}")
            for c in costs
        )

    def query_tag_costs(
        self, account_id: str, tag_key: str, start: dt.date, end: dt.date
    ) -> list[TagCost]:
        prefix = f"TAGCOST#{tag_key}#"
        items = self._t.query_between(
            _pk(account_id), f"{prefix}{start.isoformat()}", f"{prefix}{end.isoformat()}#{HIGH}"
        )
        return [_model(TagCost, i) for i in items]

    def put_forecast(self, forecast: CostForecast) -> None:
        self._t.put(_item(forecast, _pk(forecast.account_id), f"FORECAST#{forecast.month}"))

    def get_forecast(self, account_id: str, month: str) -> CostForecast | None:
        item = self._t.get(_pk(account_id), f"FORECAST#{month}")
        return _model(CostForecast, item) if item else None


class DynamoResourceRepository:
    def __init__(self, t: _Table) -> None:
        self._t = t

    def list(self, account_id: str, type: ResourceType | None = None) -> list[Resource]:
        prefix = f"RES#{type.value}#" if type else "RES#"
        return [_model(Resource, i) for i in self._t.query_prefix(_pk(account_id), prefix)]

    def replace_inventory(self, account_id: str, resources: Iterable[Resource]) -> None:
        pk = _pk(account_id)
        new_items = {f"RES#{r.key}": _item(r, pk, f"RES#{r.key}") for r in resources}
        existing = {
            i["SK"] for i in self._t.query_prefix(pk, "RES#", ProjectionExpression="PK, SK")
        }
        self._t.put_batch(new_items.values())
        self._t.delete_batch((pk, sk) for sk in existing - new_items.keys())


class DynamoFindingRepository:
    def __init__(self, t: _Table) -> None:
        self._t = t

    def list(self, account_id: str, status: FindingStatus | None = None) -> list[Finding]:
        findings = (_model(Finding, i) for i in self._t.query_prefix(_pk(account_id), "FINDING#"))
        return [f for f in findings if status is None or f.status == status]

    def get(self, account_id: str, finding_id: str) -> Finding | None:
        item = self._t.get(_pk(account_id), f"FINDING#{finding_id}")
        return _model(Finding, item) if item else None

    def put_many(self, findings: Iterable[Finding]) -> None:
        self._t.put_batch(_item(f, _pk(f.account_id), f"FINDING#{f.id}") for f in findings)

    def update_status(self, account_id: str, finding_id: str, status: FindingStatus) -> Finding:
        try:
            resp = self._t.table.update_item(
                Key={"PK": _pk(account_id), "SK": f"FINDING#{finding_id}"},
                UpdateExpression="SET #s = :s",
                ConditionExpression=Attr("PK").exists(),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": status.value},
                ReturnValues="ALL_NEW",
            )
        except ClientError as err:
            if _is_conditional_failure(err):
                raise NotFoundError(finding_id) from err
            raise
        return _model(Finding, resp["Attributes"])


class DynamoBudgetRepository:
    def __init__(self, t: _Table) -> None:
        self._t = t

    def get(self, account_id: str) -> Budget | None:
        item = self._t.get(_pk(account_id), "BUDGET")
        return _model(Budget, item) if item else None

    def put(self, budget: Budget) -> None:
        self._t.put(_item(budget, _pk(budget.account_id), "BUDGET"))


class DynamoAlertRepository:
    def __init__(self, t: _Table) -> None:
        self._t = t

    def list(self, account_id: str, limit: int = 100) -> list[Alert]:
        items = self._t.query_prefix(_pk(account_id), "ALERT#", ScanIndexForward=False)
        alerts: list[Alert] = []
        for item in items:
            alerts.append(_model(Alert, item))
            if len(alerts) >= limit:
                break
        return alerts

    def put(self, alert: Alert) -> None:
        sk = f"ALERT#{format_datetime(alert.created_at)}#{alert.id}"
        self._t.put(_item(alert, _pk(alert.account_id), sk))

    def claim_dedup(self, account_id: str, key: str, expires_at: datetime | None = None) -> bool:
        item: dict[str, Any] = {"PK": _pk(account_id), "SK": f"DEDUP#{key}", "key": key}
        if expires_at is not None:
            item[TTL_ATTRIBUTE] = int(expires_at.timestamp())  # DynamoDB TTL deletes it later
        try:
            self._t.put(item, ConditionExpression=Attr("PK").not_exists())
            return True
        except ClientError as err:
            if _is_conditional_failure(err):
                return False
            raise


class DynamoSettingsRepository:
    def __init__(self, t: _Table) -> None:
        self._t = t

    def get(self, account_id: str) -> AccountSettings:
        item = self._t.get(_pk(account_id), "SETTINGS")
        return _model(AccountSettings, item) if item else AccountSettings(account_id=account_id)

    def put(self, settings: AccountSettings) -> None:
        self._t.put(_item(settings, _pk(settings.account_id), "SETTINGS"))


class DynamoRunRepository:
    def __init__(self, t: _Table) -> None:
        self._t = t

    def put(self, run: CollectionRun) -> None:
        sk = f"RUN#{format_datetime(run.started_at)}#{run.id}"
        self._t.put(_item(run, _pk(run.account_id), sk))

    def list(self, account_id: str, limit: int = 20) -> list[CollectionRun]:
        items = self._t.query_prefix(_pk(account_id), "RUN#", ScanIndexForward=False, Limit=limit)
        runs: list[CollectionRun] = []
        for item in items:
            runs.append(_model(CollectionRun, item))
            if len(runs) >= limit:
                break
        return runs


def create_dynamodb_repositories(table: Any) -> Repositories:
    """`table` is a boto3 `dynamodb.Table` resource."""
    t = _Table(table)
    return Repositories(
        accounts=DynamoAccountRepository(t),
        costs=DynamoCostRepository(t),
        resources=DynamoResourceRepository(t),
        findings=DynamoFindingRepository(t),
        budgets=DynamoBudgetRepository(t),
        alerts=DynamoAlertRepository(t),
        settings=DynamoSettingsRepository(t),
        runs=DynamoRunRepository(t),
    )


def create_table(dynamodb: Any, table_name: str) -> Any:
    """Create the table (tests, DynamoDB Local). In AWS the table is created by CDK with the same
    schema."""
    table = dynamodb.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": name, "AttributeType": "S"}
            for name in ("PK", "SK", "GSI1PK", "GSI1SK")
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": ACCOUNTS_GSI,
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )
    table.wait_until_exists()
    dynamodb.meta.client.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE},
    )
    return table
