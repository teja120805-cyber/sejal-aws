"""Static price table loaded from prices.json.

Regions missing from the table fall back to `fallback_region` (us-east-1), which is usually the
cheapest region, so estimates for unknown regions err on the conservative side.
"""

import json
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Any


class PriceCatalog:
    def __init__(self, data: dict[str, Any]) -> None:
        meta = data["_meta"]
        self.hours_per_month = Decimal(str(meta.get("hours_per_month", 730)))
        self.fallback_region: str = meta["fallback_region"]
        self._regions: dict[str, dict[str, Any]] = data["regions"]

    @property
    def regions(self) -> list[str]:
        return sorted(self._regions)

    def _region(self, region: str) -> dict[str, Any]:
        return self._regions.get(region) or self._regions[self.fallback_region]

    def _lookup(self, region: str, table: str, key: str | None = None) -> Decimal | None:
        value = self._region(region).get(table)
        if key is not None:
            value = value.get(key) if isinstance(value, dict) else None
        return None if value is None else Decimal(str(value))

    def ec2_hourly(self, region: str, instance_type: str) -> Decimal | None:
        return self._lookup(region, "ec2_hourly", instance_type)

    def ebs_gb_month(self, region: str, volume_type: str) -> Decimal | None:
        return self._lookup(region, "ebs_gb_month", volume_type)

    def snapshot_gb_month(self, region: str) -> Decimal:
        return self._lookup(region, "ebs_snapshot_gb_month") or Decimal(0)

    def public_ipv4_hourly(self, region: str) -> Decimal:
        return self._lookup(region, "public_ipv4_hourly") or Decimal(0)

    def rds_hourly(self, region: str, instance_class: str) -> Decimal | None:
        return self._lookup(region, "rds_hourly", instance_class)

    def rds_storage_gb_month(self, region: str) -> Decimal:
        return self._lookup(region, "rds_storage_gb_month") or Decimal(0)

    def s3_gb_month(self, region: str, storage_class: str = "STANDARD") -> Decimal | None:
        return self._lookup(region, "s3_gb_month", storage_class)

    def lambda_gb_second(self, region: str) -> Decimal:
        return self._lookup(region, "lambda_gb_second") or Decimal(0)

    def lambda_per_million_requests(self, region: str) -> Decimal:
        return self._lookup(region, "lambda_per_million_requests") or Decimal(0)


@lru_cache
def default_catalog() -> PriceCatalog:
    text = resources.files("finops.pricing").joinpath("prices.json").read_text(encoding="utf-8")
    return PriceCatalog(json.loads(text))
