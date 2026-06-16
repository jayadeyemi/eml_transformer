import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

from eml_transformer.cloud.aws.config import load_aws_runtime_config
from eml_transformer.deployment.config import (
    build_runtime_environment,
    deployment_config_warnings,
    deployment_matrix,
    deployment_metadata,
    load_deployment_config,
    render_runtime_config,
    validate_deployment_config,
)


LOG_ROOT = Path(
    os.getenv(
        "DEPLOYMENT_TEST_LOG_DIR",
        "artifacts/aws_test_results/deployment_config",
    )
)


def deployment_cases():
    return [
        (path, load_deployment_config(path))
        for path in sorted(Path("configs/deployments").glob("*.yaml"))
    ]


def _safe_name(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in value
    )


def write_json_log(category: str, name: str, payload: dict) -> Path:
    output_dir = LOG_ROOT / category
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_name(name)}.json"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return output_path


class DeploymentConfigContractTests(unittest.TestCase):
    def test_all_deployments_validate_render_matrix_and_log_contracts(self):
        cases = deployment_cases()
        self.assertGreater(len(cases), 0)

        for path, loaded in cases:
            cfg = loaded.config
            metadata = deployment_metadata(cfg)

            with self.subTest(deployment=metadata["deployment_name"], path=path):
                errors = validate_deployment_config(cfg)
                runtime = render_runtime_config(cfg)
                env = build_runtime_environment(cfg)
                aws_config = load_aws_runtime_config(runtime)
                matrix = deployment_matrix(cfg)
                services = {item["service"]: item for item in matrix["services"]}

                output_path = write_json_log(
                    "deployments",
                    metadata["deployment_name"],
                    {
                        "api": "deployment_config_contract",
                        "inputs": {
                            "deployment_path": path.as_posix(),
                            "deployment_config": cfg,
                            "layers": [layer.as_posix() for layer in loaded.layers],
                        },
                        "outputs": {
                            "metadata": metadata,
                            "validation_errors": errors,
                            "warnings": deployment_config_warnings(cfg),
                            "runtime_config": runtime,
                            "runtime_environment": env,
                            "deployment_matrix": matrix,
                        },
                    },
                )
                print(f"DEPLOYMENT_CONFIG_LOG {output_path.as_posix()}", flush=True)

                self.assertEqual(errors, [])
                self.assertEqual(metadata["stack_name"], cfg["infra"]["stack_name"])
                self.assertEqual(metadata["region"], cfg["infra"]["region"])
                self.assertEqual(runtime["aws"]["infra_stack"], cfg["infra"]["stack_name"])
                self.assertEqual(env["INFRA_STACK"], cfg["infra"]["stack_name"])
                self.assertEqual(aws_config.infra_stack, cfg["infra"]["stack_name"])
                self.assertNotIn("estimated_monthly_cost", matrix)

                if "url_fetch_worker" in services:
                    self.assertEqual(
                        services["url_fetch_worker"]["job_definition_env_key"],
                        "BATCH_JOB_DEFINITION_URL_FETCH_WORKER",
                    )

                if cfg["infra"]["engine"] == "cdk":
                    self.assertEqual(runtime["aws"]["cdk_stack"], cfg["infra"]["stack_name"])
                    self.assertEqual(env["CDK_STACK"], cfg["infra"]["stack_name"])
                    self.assertEqual(aws_config.cdk_stack, cfg["infra"]["stack_name"])
                    self.assertIn("batch_job_definitions", runtime["orchestration"])
                    self.assertIn("BATCH_JOB_DEFINITION_GDELT_DISCOVERY", env)
                    self.assertIn("BATCH_JOB_DEFINITION_S3_RESTORE_OPERATOR", env)
                else:
                    self.assertIsNone(runtime["aws"]["cdk_stack"])
                    self.assertEqual(env["CDK_STACK"], "")
                    self.assertEqual(runtime["orchestration"]["batch_job_definitions"], {})

                if cfg.get("notifications", {}).get("sns", {}).get("enabled", False):
                    self.assertIsNotNone(runtime["notifications"]["sns_topic_arn"])
                    self.assertEqual(env["SNS_TOPIC_ARN"], runtime["notifications"]["sns_topic_arn"])
                    self.assertEqual(aws_config.sns_topic_arn, runtime["notifications"]["sns_topic_arn"])


if __name__ == "__main__":
    unittest.main()
