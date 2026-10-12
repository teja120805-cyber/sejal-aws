"""Generate the demo dataset and print it, export it, or load it into DynamoDB.

Examples (run from backend/):
    python scripts/seed_demo.py                       # print a summary
    python scripts/seed_demo.py --json demo.json      # export everything as JSON
    python scripts/seed_demo.py --dynamodb --endpoint-url http://localhost:8001 --create-table
                                                      # load into DynamoDB Local

Writing to a real AWS DynamoDB table needs --confirm-real-aws, so it can't happen by accident.
In demo mode (DEMO_MODE=true) the API generates this data in memory by itself; no seeding needed.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from finops.demo import generate_demo_dataset, load_dataset


def _export(ds: Any) -> dict[str, Any]:
    def dump(models: list[Any]) -> list[dict[str, Any]]:
        return [m.model_dump(mode="json") for m in models]

    return {
        "summary": ds.summary,
        "account": ds.account.model_dump(mode="json"),
        "budget": ds.budget.model_dump(mode="json"),
        "settings": ds.settings.model_dump(mode="json"),
        "forecast": ds.forecast.model_dump(mode="json"),
        "daily_costs": dump(ds.daily_costs),
        "tag_costs": dump(ds.tag_costs),
        "resources": dump(ds.resources),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="write the dataset to this JSON file")
    parser.add_argument("--dynamodb", action="store_true", help="load into a DynamoDB table")
    parser.add_argument("--table", default="finops", help="DynamoDB table name (default: finops)")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--endpoint-url", help="e.g. http://localhost:8001 for DynamoDB Local")
    parser.add_argument("--create-table", action="store_true", help="create the table first")
    parser.add_argument(
        "--confirm-real-aws",
        action="store_true",
        help="required to write to real AWS (no --endpoint-url)",
    )
    args = parser.parse_args(argv)

    ds = generate_demo_dataset()
    print(json.dumps(ds.summary, indent=2))

    if args.json:
        args.json.write_text(json.dumps(_export(ds), indent=2), encoding="utf-8")
        print(f"wrote {args.json}")

    if args.dynamodb:
        if not args.endpoint_url and not args.confirm_real_aws:
            print(
                "refusing to write to real AWS without --confirm-real-aws "
                "(or pass --endpoint-url for DynamoDB Local)",
                file=sys.stderr,
            )
            return 2
        import boto3

        from finops.repositories.dynamodb import create_dynamodb_repositories, create_table

        ddb = boto3.resource("dynamodb", region_name=args.region, endpoint_url=args.endpoint_url)
        table = create_table(ddb, args.table) if args.create_table else ddb.Table(args.table)
        load_dataset(create_dynamodb_repositories(table), ds)
        print(f"loaded demo data into DynamoDB table {args.table!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
