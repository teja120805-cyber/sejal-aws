import json
from decimal import Decimal

import boto3
from botocore.stub import ANY, Stubber

from finops.pricing import default_catalog
from finops.pricing.pricing_api import PricingApiCatalog, build_catalog


def product(price: str) -> str:
    return json.dumps(
        {
            "product": {"attributes": {"instanceType": "m5.large"}},
            "terms": {
                "OnDemand": {
                    "X.Y": {"priceDimensions": {"X.Y.Z": {"pricePerUnit": {"USD": price}}}}
                }
            },
        }
    )


def make(responses):
    client = boto3.client(
        "pricing", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    stub = Stubber(client)
    for r in responses:
        if isinstance(r, Exception):
            stub.add_client_error("get_products", "ThrottlingException")
        else:
            stub.add_response(
                "get_products",
                {"PriceList": r, "FormatVersion": "aws_v1"},
                {"ServiceCode": "AmazonEC2", "Filters": ANY, "MaxResults": 1},
            )
    stub.activate()
    return PricingApiCatalog(default_catalog(), client), stub


def test_live_price_is_used_and_cached():
    cat, stub = make([[product("0.1100000000")]])
    assert cat.ec2_hourly("ap-south-1", "m5.large") == Decimal("0.11")
    assert cat.ec2_hourly("ap-south-1", "m5.large") == Decimal("0.11")  # cached, no 2nd call
    stub.assert_no_pending_responses()
    # other lookups still come from the static table
    assert cat.ebs_gb_month("ap-south-1", "gp3") == Decimal("0.0912")


def test_falls_back_to_static_table_when_missing_or_failing():
    cat, _ = make([[], RuntimeError()])
    assert cat.ec2_hourly("ap-south-1", "m5.large") == Decimal("0.101")  # empty result
    assert cat.ec2_hourly("us-east-1", "m5.large") == Decimal("0.096")  # API error


def test_build_catalog_static_by_default():
    assert build_catalog(False) is default_catalog()
    session = boto3.Session(aws_access_key_id="x", aws_secret_access_key="x")
    assert isinstance(build_catalog(True, session), PricingApiCatalog)
