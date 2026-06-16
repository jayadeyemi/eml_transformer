import os
import sys
import unittest
import shutil
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

if shutil.which("node") is None:
    App = None
    Match = None
    Template = None
else:
    try:
        from aws_cdk import App
        from aws_cdk.assertions import Match, Template
    except ImportError:  # pragma: no cover - exercised in CI when CDK deps exist.
        App = None
        Match = None
        Template = None

from eml_transformer.deployment.config import load_deployment_config


LOG_ROOT = Path(
    os.getenv(
        "CDK_TEST_LOG_DIR",
        "artifacts/aws_test_results/cdk_stack",
    )
)


def cdk_deployment_cases():
    cases = []
    for path in sorted(Path("configs/deployments").glob("*.yaml")):
        loaded = load_deployment_config(path)
        if loaded.config.get("infra", {}).get("engine") == "cdk":
            cases.append((path, loaded))
    return cases


def runtime_secret_env(loaded) -> dict[str, str]:
    env = {}
    for secret_name, secret_cfg in loaded.config.get("runtime_secrets", {}).items():
        env_key = secret_cfg.get("secret_arn_env")
        if env_key:
            env[env_key] = (
                "arn:aws:secretsmanager:us-east-1:123456789012:"
                f"secret:{secret_name.lower()}-test"
            )
    return env


def stack_id_for(loaded) -> str:
    name = loaded.config.get("deployment", {}).get("name", "deployment")
    return "".join(part.title() for part in name.replace("_", "-").split("-")) + "Stack"


def _safe_name(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in value
    )


def write_json_log(name: str, payload: dict) -> Path:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = LOG_ROOT / f"{_safe_name(name)}.json"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return output_path


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "cdk").exists() and (parent / "src").exists():
            return parent
    raise RuntimeError("Could not locate repository root from test path")


class CdkStackTests(unittest.TestCase):
    @unittest.skipIf(App is None, "aws-cdk-lib or Node.js is not installed")
    def test_all_cdk_deployments_synthesize_and_log_resources(self):
        cdk_root = repo_root() / "infra" / "cdk"
        sys.path.insert(0, str(cdk_root))

        from eml_transformer_cdk.stack import EmlTransformerCollectionStack

        cases = cdk_deployment_cases()
        self.assertGreater(len(cases), 0)

        for path, loaded in cases:
            deployment_name = loaded.config.get("deployment", {}).get("name")
            with self.subTest(deployment=deployment_name, path=path):
                secret_env = runtime_secret_env(loaded)
                with patch.dict(os.environ, secret_env):
                    app = App()
                    stack = EmlTransformerCollectionStack(
                        app,
                        stack_id_for(loaded),
                        deployment_config=loaded.config,
                    )
                    template = Template.from_stack(stack)

                template_json = template.to_json()
                resource_counts = {}
                for resource in template_json["Resources"].values():
                    resource_type = resource["Type"]
                    resource_counts[resource_type] = resource_counts.get(resource_type, 0) + 1

                state_machine_definition_text = "\n".join(
                    json.dumps(
                        resource["Properties"].get("DefinitionString", {}),
                        sort_keys=True,
                    )
                    for resource in template_json["Resources"].values()
                    if resource["Type"] == "AWS::StepFunctions::StateMachine"
                )

                print(
                    "CDK_DEPLOYMENT_SUMMARY "
                    + json.dumps(
                        {
                            "deployment": deployment_name,
                            "path": path.as_posix(),
                            "stack_name": loaded.config["infra"]["stack_name"],
                            "resource_counts": resource_counts,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                output_path = write_json_log(
                    deployment_name,
                    {
                        "api": "cdk_stack_synth",
                        "inputs": {
                            "deployment_path": path.as_posix(),
                            "deployment_config": loaded.config,
                            "stack_id": stack_id_for(loaded),
                            "runtime_secret_env_keys": sorted(secret_env),
                        },
                        "outputs": {
                            "resource_counts": resource_counts,
                            "cloudformation_template": template_json,
                        },
                    },
                )
                print(f"CDK_DEPLOYMENT_LOG {output_path.as_posix()}", flush=True)

                template.resource_count_is("AWS::S3::Bucket", 1)
                template.resource_count_is("AWS::SQS::Queue", 2)
                template.resource_count_is("AWS::DynamoDB::Table", 3)
                template.resource_count_is("AWS::Batch::JobDefinition", 8)
                template.resource_count_is("AWS::StepFunctions::StateMachine", 3)

                sns_cfg = loaded.config.get("notifications", {}).get("sns", {})
                if sns_cfg.get("enabled", False):
                    template.resource_count_is("AWS::SNS::Topic", 1)
                    template.has_resource_properties(
                        "AWS::SNS::Subscription",
                        {
                            "Protocol": "email",
                            "Endpoint": Match.any_value(),
                        },
                    )
                    self.assertIn("sns:publish", state_machine_definition_text)

                for secret_name, secret_cfg in loaded.config.get("runtime_secrets", {}).items():
                    env_key = secret_cfg["secret_arn_env"]
                    template.has_resource_properties(
                        "AWS::Batch::JobDefinition",
                        {
                            "ContainerProperties": Match.object_like(
                                {
                                    "Secrets": Match.array_with(
                                        [
                                            {
                                                "Name": secret_name,
                                                "ValueFrom": secret_env[env_key],
                                            }
                                        ]
                                    )
                                }
                            )
                        },
                    )

                self.assertIn("ingest_result", state_machine_definition_text)
                self.assertIn("standardize_result", state_machine_definition_text)
                self.assertIn("backfill_result", state_machine_definition_text)
                alarms = [
                    resource["Properties"]
                    for resource in template_json["Resources"].values()
                    if resource["Type"] == "AWS::CloudWatch::Alarm"
                ]
                has_alarm_actions = any(
                    bool(properties.get("AlarmActions")) for properties in alarms
                )
                self.assertEqual(has_alarm_actions, bool(sns_cfg.get("enabled", False)))

                job_definitions = [
                    resource["Properties"]
                    for resource in template_json["Resources"].values()
                    if resource["Type"] == "AWS::Batch::JobDefinition"
                ]
                for properties in job_definitions:
                    env_names = {
                        item["Name"]
                        for item in properties["ContainerProperties"].get("Environment", [])
                    }
                    self.assertNotIn("STATE_MACHINE_ARN", env_names)
                    self.assertNotIn("SOURCE_WORKFLOW_ARN", env_names)
                    self.assertNotIn("BACKFILL_WORKFLOW_ARN", env_names)
                    self.assertFalse(
                        any(name.startswith("BATCH_JOB_DEFINITION_") for name in env_names)
                    )


if __name__ == "__main__":
    unittest.main()
