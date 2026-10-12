"""Optional live EC2 price lookup through the AWS Pricing API (USE_PRICING_API=true).

The Pricing API is free, called with the platform's own credentials (not the customer's), and
served from a few regions only - ap-south-1 (Mumbai) is one of them. Results are cached per
(region, instance type); anything not found falls back to the static table.
"""

import json
import logging
from decimal import Decimal
from typing import Any

from finops.pricing.catalog import PriceCatalog

logger = logging.getLogger(__name__)

PRICING_API_REGION = "ap-south-1"


class PricingApiCatalog(PriceCatalog):
    def __init__(self, base: PriceCatalog, client: Any) -> None:
        self.__dict__.update(base.__dict__)
        self._client = client
        self._cache: dict[tuple[str, str], Decimal | None] = {}

    def ec2_hourly(self, region: str, instance_type: str) -> Decimal | None:
        key = (region, instance_type)
        if key not in self._cache:
            try:
                self._cache[key] = self._lookup_ec2(region, instance_type)
            except Exception as err:  # network/permissions: never break estimates
                logger.warning(
                    "pricing API lookup failed for %s %s: %s", region, instance_type, err
                )
                self._cache[key] = None
        return self._cache[key] or super().ec2_hourly(region, instance_type)

    def _lookup_ec2(self, region: str, instance_type: str) -> Decimal | None:
        filters = {
            "instanceType": instance_type,
            "regionCode": region,
            "operatingSystem": "Linux",
            "tenancy": "Shared",
            "preInstalledSw": "NA",
            "capacitystatus": "Used",
        }
        resp = self._client.get_products(
            ServiceCode="AmazonEC2",
            Filters=[{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in filters.items()],
            MaxResults=1,
        )
        for raw in resp.get("PriceList", []):
            product = json.loads(raw) if isinstance(raw, str) else raw
            for term in product.get("terms", {}).get("OnDemand", {}).values():
                for dim in term.get("priceDimensions", {}).values():
                    usd = dim.get("pricePerUnit", {}).get("USD")
                    if usd and Decimal(usd) > 0:
                        return Decimal(usd)
        return None


def build_catalog(use_pricing_api: bool, session: Any | None = None) -> PriceCatalog:
    from finops.pricing.catalog import default_catalog

    base = default_catalog()
    if not use_pricing_api:
        return base
    import boto3

    client = (session or boto3.Session()).client("pricing", region_name=PRICING_API_REGION)
    return PricingApiCatalog(base, client)
