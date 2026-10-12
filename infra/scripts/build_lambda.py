"""Build the Lambda deployment package into infra/build/lambda (no Docker needed).

Installs the `finops` package and its dependencies as Linux ARM64 (Graviton) wheels for
Python 3.12, copies the collector entrypoint and the customer CloudFormation templates.
boto3/botocore are excluded: the Lambda runtime already provides them.

    python infra/scripts/build_lambda.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "infra" / "build" / "lambda"
EXCLUDE = ("boto3", "botocore", "s3transfer", "jmespath")


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    pip = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--target",
        str(OUT),
        "--platform",
        "manylinux2014_aarch64",
        "--implementation",
        "cp",
        "--python-version",
        "3.12",
        "--only-binary=:all:",
        "--upgrade",
    ]
    # Install the app without deps first, then its runtime deps (minus the runtime-provided ones).
    subprocess.run([*pip, "--no-deps", str(ROOT / "backend")], check=True)
    deps = [
        "fastapi>=0.115,<1.0",
        "mangum>=0.19,<1.0",
        "numpy>=2.0",
        "pydantic>=2.8",
        "pydantic-settings>=2.4",
        "pyjwt[crypto]>=2.9",
    ]
    subprocess.run([*pip, *deps], check=True)

    for name in EXCLUDE:
        for path in OUT.glob(f"{name}*"):
            shutil.rmtree(path, ignore_errors=True)
    for path in OUT.rglob("__pycache__"):
        shutil.rmtree(path, ignore_errors=True)

    shutil.copy(ROOT / "collector" / "handler.py", OUT / "collector_handler.py")
    templates = OUT / "finops" / "customer_templates"
    templates.mkdir(exist_ok=True)
    for tpl in (ROOT / "infra" / "customer-templates").glob("*.yaml"):
        shutil.copy(tpl, templates / tpl.name)

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"Lambda package built in {OUT} ({size:.1f} MB unzipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
