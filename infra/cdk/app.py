from __future__ import annotations

import os
import sys
from pathlib import Path

from aws_cdk import App, Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from eml_transformer.deployment.config import (  # noqa: E402
    assert_valid_deployment_config,
    load_deployment_config,
)
from eml_transformer_cdk.stack import EmlTransformerCollectionStack  # noqa: E402


app = App()
deployment_config = (
    app.node.try_get_context("deployment_config")
    or os.environ.get("DEPLOYMENT_CONFIG")
)

if not deployment_config:
    raise ValueError(
        "A deployment config is required. Pass "
        "-c deployment_config=configs/deployments/<name>.yaml or set "
        "DEPLOYMENT_CONFIG."
    )

deployment_path = Path(deployment_config)

if not deployment_path.is_absolute():
    deployment_path = REPO_ROOT / deployment_path

loaded = load_deployment_config(deployment_path)
assert_valid_deployment_config(loaded.config)
infra = loaded.config["infra"]

# Support immutable image tags injected by CI: cdk deploy -c image_tag=<sha>
image_tag = app.node.try_get_context("image_tag") or "latest"
loaded.config.setdefault("runtime", {})["image_tag"] = image_tag

schedule_test_expression = (
    app.node.try_get_context("schedule_test_expression")
    or os.environ.get("SCHEDULE_TEST_EXPRESSION")
)
if schedule_test_expression:
    services = loaded.config.setdefault("services", {})
    for service_name in ("gdelt_discovery", "ingest"):
        service = services.setdefault(service_name, {})
        schedule = service.setdefault("schedule", {})
        if schedule.get("enabled", False):
            schedule["expression"] = schedule_test_expression

subnet_ids = os.environ.get("BATCH_SUBNET_IDS")
security_group_ids = os.environ.get("BATCH_SECURITY_GROUP_IDS")
if subnet_ids or security_group_ids:
    network = loaded.config.setdefault("network", {})
    if subnet_ids:
        network["subnet_ids"] = [
            item.strip() for item in subnet_ids.split(",") if item.strip()
        ]
    if security_group_ids:
        network["security_group_ids"] = [
            item.strip() for item in security_group_ids.split(",") if item.strip()
        ]

# Honour an explicit AWS profile passed via context:
#   cdk synth -c deployment_config=... -c aws_profile=eml-dev
# Falls back to the AWS_PROFILE env var if not supplied.
aws_profile = app.node.try_get_context("aws_profile") or os.environ.get("AWS_PROFILE")
if aws_profile:
    os.environ["AWS_PROFILE"] = aws_profile

EmlTransformerCollectionStack(
    app,
    infra["stack_name"],
    deployment_config=loaded.config,
    env=Environment(
        account=str(infra["account_id"]) if infra.get("account_id") else None,
        region=infra.get("region", "us-east-1"),
    ),
)

app.synth()
