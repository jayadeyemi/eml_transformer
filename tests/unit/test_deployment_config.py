import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from eml_transformer.cli import _has_failed_result
from eml_transformer.cloud.aws.config import load_aws_runtime_config
from eml_transformer.deployment.config import (
    _pascal_to_snake,
    build_runtime_environment,
    deployment_config_warnings,
    deployment_metadata,
    load_deployment_config,
    render_runtime_config,
    render_runtime_config_from_cfn_outputs,
    validate_deployment_config,
)
from eml_transformer.ingestion.sources.newsapi import NewsAPISource
from eml_transformer.utils.config import build_source_configs, load_config


def deployment_cases():
    return [
        (path, load_deployment_config(path))
        for path in sorted(Path("configs/deployments").glob("*.yaml"))
    ]


def deployment_case_by_engine(engine: str):
    for path, loaded in deployment_cases():
        if loaded.config.get("infra", {}).get("engine") == engine:
            return path, loaded
    raise AssertionError(f"Deployment not found by engine: {engine}")


class DeploymentConfigUnitTests(unittest.TestCase):
    def test_infra_stack_roundtrips_through_load_aws_runtime_config(self):
        aws_config = load_aws_runtime_config(
            {
                "aws": {
                    "region": "us-east-1",
                    "environment": "test",
                    "infra_stack": "primary-stack",
                }
            }
        )

        self.assertEqual(aws_config.infra_stack, "primary-stack")
        self.assertEqual(aws_config.cdk_stack, "primary-stack")

    def test_deployment_metadata_comes_from_config_not_path_names(self):
        for path, loaded in deployment_cases():
            with self.subTest(path=path):
                metadata = deployment_metadata(loaded.config)
                expected_outputs = (
                    f"configs/generated/{metadata['deployment_name']}.cfn-outputs.json"
                )

                self.assertEqual(metadata["deployment_name"], loaded.config["deployment"]["name"])
                self.assertEqual(metadata["stack_name"], loaded.config["infra"]["stack_name"])
                self.assertEqual(metadata["region"], loaded.config["infra"]["region"])
                self.assertEqual(
                    metadata["runtime_config_path"],
                    loaded.config.get("runtime", {}).get(
                        "config_path",
                        f"configs/generated/{metadata['deployment_name']}.runtime.yaml",
                    ),
                )
                self.assertEqual(metadata["cfn_outputs_path"], expected_outputs)

    def test_local_base_config_accepts_article_dlq_env_override(self):
        dlq_url = "https://sqs.us-east-1.amazonaws.com/123/my-stack-url-fetch-dlq"

        with patch.dict("os.environ", {"ARTICLE_URL_DLQ_URL": dlq_url}):
            cfg = load_config("configs/local.yaml")

        self.assertEqual(cfg["queues"]["article_url_dlq_url"], dlq_url)

    def test_local_base_config_layers_source_files(self):
        cfg = load_config("configs/local.yaml")
        source_configs = build_source_configs(cfg)

        self.assertEqual(cfg["infra"]["engine"], "local")
        self.assertIn("weather_alerts", source_configs)
        self.assertIn("newsapi", source_configs)
        self.assertNotIn("gdelt", source_configs)
        self.assertEqual(cfg["storage"]["backend"], "local")

    def test_local_deployment_validates_and_renders_without_aws_resources(self):
        _, loaded = deployment_case_by_engine("local")
        runtime = render_runtime_config(loaded.config)
        env = build_runtime_environment(loaded.config)

        self.assertEqual(validate_deployment_config(loaded.config), [])
        self.assertEqual(loaded.config["infra"]["engine"], "local")
        self.assertEqual(loaded.config["cost"]["monthly_budget_usd"], 0)
        self.assertEqual(runtime["storage"], {"backend": "local", "base_dir": "data"})
        self.assertIsNone(runtime["queues"]["url_fetch_queue_url"])
        self.assertIsNone(runtime["queues"]["article_url_dlq_url"])
        self.assertEqual(runtime["orchestration"]["batch_job_definitions"], {})
        self.assertEqual(env["DATA_BUCKET"], "")
        self.assertEqual(env["CDK_STACK"], "")

    def test_hpc_deployment_renders_without_fake_aws_resources(self):
        _, loaded = deployment_case_by_engine("hpc")
        runtime = render_runtime_config(loaded.config)
        env = build_runtime_environment(loaded.config)

        self.assertEqual(validate_deployment_config(loaded.config), [])
        self.assertEqual(loaded.config["cost"]["monthly_budget_usd"], 0)
        self.assertEqual(runtime["storage"], {"backend": "local", "base_dir": "data"})
        self.assertIsNone(runtime["queues"]["url_fetch_queue_url"])
        self.assertIsNone(runtime["queues"]["article_url_dlq_url"])
        self.assertIsNone(runtime["orchestration"]["batch_job_queue"])
        self.assertEqual(runtime["orchestration"]["batch_job_definitions"], {})
        self.assertEqual(env["URL_FETCH_QUEUE_URL"], "")
        self.assertEqual(env["BATCH_JOB_QUEUE"], "")

    def test_hpc_base_config_contains_hpc_settings(self):
        hpc_cfg = load_config("configs/hpc.yaml")

        self.assertEqual(hpc_cfg["infra"]["engine"], "hpc")
        self.assertEqual(hpc_cfg["storage"], {"backend": "local", "base_dir": "data"})
        self.assertFalse(hpc_cfg["services"]["gdelt_discovery"]["enabled"])
        self.assertFalse(hpc_cfg["services"]["url_fetch_worker"]["enabled"])
        self.assertTrue(hpc_cfg["services"]["embed"]["enabled"])
        self.assertEqual(hpc_cfg["embeddings"]["device"], "cuda")
        self.assertIsNone(hpc_cfg["aws"]["cdk_stack"])

    def test_config_root_contains_only_base_profiles(self):
        base_files = sorted(path.name for path in Path("configs").glob("*.yaml"))

        self.assertEqual(base_files, ["aws.yaml", "dev.yaml", "hpc.yaml", "local.yaml"])
        self.assertFalse(Path("configs/run-machine").exists())

    def test_base_profiles_own_source_layers(self):
        import yaml

        for path in [Path("configs/aws.yaml"), Path("configs/hpc.yaml"), Path("configs/local.yaml")]:
            with self.subTest(path=path):
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))

                self.assertIn("base", doc)
                self.assertIn("source_configs", doc["base"])
                self.assertNotIn("run_machine", doc)
                self.assertNotIn("base_config", doc["base"])
                self.assertNotIn("runtime", doc)

    def test_base_and_environment_layers_are_not_used(self):
        self.assertFalse(Path("configs/base.yaml").exists())
        self.assertFalse(Path("configs/environments").exists())

    def test_deployment_layers_do_not_duplicate_base_layers(self):
        for path in Path("configs/deployments").glob("*.yaml"):
            with self.subTest(path=path):
                loaded = load_deployment_config(path)

                self.assertEqual(len(loaded.layers), len(set(loaded.layers)))

    def test_deployments_use_base_not_old_layer_keys(self):
        import yaml

        for path in Path("configs/deployments").glob("*.yaml"):
            with self.subTest(path=path):
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
                deployment = doc.get("deployment", {})

                self.assertIn("base", deployment)
                self.assertNotIn("run_machine", deployment)
                self.assertNotIn("runtime" + "_profile", deployment)
                self.assertNotIn("base_config", deployment)

    def test_runtime_config_path_is_only_for_generated_aws_runtime_files(self):
        import yaml

        for path in Path("configs/deployments").glob("*.yaml"):
            with self.subTest(path=path):
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
                runtime_path = doc.get("runtime", {}).get("config_path")

                if runtime_path is None:
                    continue

                self.assertTrue(runtime_path.startswith("configs/generated/"))


class PlaceholderNetworkValidationTests(unittest.TestCase):
    def _minimal_cdk_cfg(self, **overrides) -> dict:
        base = {
            "infra": {
                "engine": "cdk",
                "stack_name": "test-stack",
                "region": "us-east-1",
                "environment": "dev",
                "account_id": None,
            },
            "cost": {"monthly_budget_usd": 10, "alert_emails": []},
            "storage": {
                "backend": "s3",
                "lifecycle": {
                    "bronze_glacier_ir_days": 90,
                    "bronze_deep_archive_days": 365,
                },
            },
            "services": {
                "gdelt_discovery": {"enabled": True, "compute": {"vcpu": 1, "memory_mib": 2048, "timeout_seconds": 3600}}
            },
            "sources": {},
            "network": {
                "subnet_ids": ["subnet-replace-me"],
                "security_group_ids": ["sg-replace-me"],
            },
        }
        base.update(overrides)
        return base

    def test_placeholder_subnets_produce_warning_when_no_account_id(self):
        warnings = deployment_config_warnings(self._minimal_cdk_cfg())
        self.assertTrue(any("subnet_ids" in warning for warning in warnings))

    def test_placeholder_sgs_produce_warning_when_no_account_id(self):
        warnings = deployment_config_warnings(self._minimal_cdk_cfg())
        self.assertTrue(any("security_group_ids" in warning for warning in warnings))

    def test_placeholder_subnets_produce_error_when_account_id_is_set(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["account_id"] = "123456789012"
        errors = validate_deployment_config(cfg)
        self.assertTrue(any("subnet_ids" in error for error in errors))

    def test_placeholder_sgs_produce_error_when_account_id_is_set(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["account_id"] = "123456789012"
        errors = validate_deployment_config(cfg)
        self.assertTrue(any("security_group_ids" in error for error in errors))

    def test_real_subnet_and_sg_pass_validation(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["account_id"] = "123456789012"
        cfg["network"]["subnet_ids"] = ["subnet-abc12345"]
        cfg["network"]["security_group_ids"] = ["sg-abc12345"]
        errors = validate_deployment_config(cfg)
        network_errors = [error for error in errors if "subnet" in error or "security_group" in error]
        self.assertEqual(network_errors, [])

    def test_prod_without_account_id_produces_warning(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["environment"] = "prod"
        warnings = deployment_config_warnings(cfg)
        self.assertTrue(any("account_id" in warning for warning in warnings))

    def test_prod_with_account_id_and_no_alert_emails_is_error(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["environment"] = "prod"
        cfg["infra"]["account_id"] = "123456789012"
        cfg["network"]["subnet_ids"] = ["subnet-abc12345"]
        cfg["network"]["security_group_ids"] = ["sg-abc12345"]
        errors = validate_deployment_config(cfg)
        self.assertTrue(any("alert_emails" in error for error in errors))

    def test_committed_cdk_configs_without_account_id_still_pass_validate_all(self):
        for path, loaded in deployment_cases():
            if loaded.config.get("infra", {}).get("engine") != "cdk":
                continue
            if loaded.config.get("infra", {}).get("account_id"):
                continue

            with self.subTest(path=path):
                self.assertEqual(validate_deployment_config(loaded.config), [])


class RenderFromCfnOutputsTests(unittest.TestCase):
    def _fake_cfn_client(self, outputs: list[dict]) -> MagicMock:
        client = MagicMock()
        client.describe_stacks.return_value = {"Stacks": [{"Outputs": outputs}]}
        return client

    def test_maps_cf_outputs_to_runtime_config_structure(self):
        import json

        runtime_env = {
            "AWS_REGION": "us-east-1",
            "EML_ENVIRONMENT": "dev",
            "INFRA_STACK": "my-stack",
            "CDK_STACK": "my-stack",
            "PROJECT": "eml_transformer",
            "CLOUDWATCH_NAMESPACE": "EMLTransformer/Collection",
            "DATA_BUCKET": "my-stack-data-123",
            "URL_FETCH_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/my-stack-url-fetch",
            "URL_STATE_TABLE": "my-stack-url-state",
            "RUN_STATE_TABLE": "my-stack-run-state",
            "DOMAIN_THROTTLE_TABLE": "my-stack-domain-throttle",
            "BATCH_JOB_QUEUE": "arn:aws:batch:us-east-1:123:job-queue/my-stack-collection",
            "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:123:stateMachine:my-stack-acquisition",
            "STORAGE_PREFIX": "",
        }
        outputs = [
            {"OutputKey": "DataBucketName", "OutputValue": "my-stack-data-123"},
            {"OutputKey": "UrlFetchQueueUrl", "OutputValue": "https://sqs.us-east-1.amazonaws.com/123/q"},
            {"OutputKey": "UrlFetchDlqUrl", "OutputValue": "https://sqs.us-east-1.amazonaws.com/123/dlq"},
            {"OutputKey": "UrlStateTable", "OutputValue": "my-stack-url-state"},
            {"OutputKey": "RunStateTable", "OutputValue": "my-stack-run-state"},
            {"OutputKey": "DomainThrottleTable", "OutputValue": "my-stack-domain-throttle"},
            {"OutputKey": "BatchJobQueue", "OutputValue": "arn:aws:batch:us-east-1:123:job-queue/q"},
            {"OutputKey": "StateMachineArn", "OutputValue": "arn:aws:states:us-east-1:123:stateMachine:sm"},
            {"OutputKey": "SnsTopicArn", "OutputValue": "arn:aws:sns:us-east-1:123:topic"},
            {"OutputKey": "IngestJobDefinition", "OutputValue": "arn:aws:batch:::ingest"},
            {"OutputKey": "StandardizeJobDefinition", "OutputValue": "arn:aws:batch:::standardize"},
            {"OutputKey": "RuntimeEnvironment", "OutputValue": json.dumps(runtime_env)},
        ]

        result = render_runtime_config_from_cfn_outputs(
            "my-stack",
            "us-east-1",
            _cfn_client=self._fake_cfn_client(outputs),
        )

        self.assertEqual(result["storage"]["bucket"], "my-stack-data-123")
        self.assertEqual(result["queues"]["url_fetch_queue_url"], "https://sqs.us-east-1.amazonaws.com/123/q")
        self.assertEqual(result["state"]["url_table"], "my-stack-url-state")
        self.assertEqual(result["state"]["domain_throttle_table"], "my-stack-domain-throttle")
        self.assertEqual(result["orchestration"]["state_machine_arn"], "arn:aws:states:us-east-1:123:stateMachine:sm")
        self.assertEqual(result["notifications"]["sns_topic_arn"], "arn:aws:sns:us-east-1:123:topic")
        self.assertIn("ingest", result["orchestration"]["batch_job_definitions"])
        self.assertIn("standardize", result["orchestration"]["batch_job_definitions"])

    def test_raises_when_stack_not_found(self):
        fake_client = MagicMock()
        fake_client.describe_stacks.return_value = {"Stacks": []}

        with self.assertRaises(ValueError):
            render_runtime_config_from_cfn_outputs("nonexistent-stack", _cfn_client=fake_client)

    def test_output_shape_matches_render_runtime_config_keys(self):
        import json

        runtime_env = {
            "AWS_REGION": "us-east-1",
            "EML_ENVIRONMENT": "dev",
            "INFRA_STACK": "s",
            "CDK_STACK": "s",
        }
        outputs = [{"OutputKey": "RuntimeEnvironment", "OutputValue": json.dumps(runtime_env)}]

        result = render_runtime_config_from_cfn_outputs("s", _cfn_client=self._fake_cfn_client(outputs))
        aws_cfg = load_aws_runtime_config(result)
        self.assertEqual(aws_cfg.region, "us-east-1")


class PascalToSnakeTests(unittest.TestCase):
    def test_simple_pascal_to_snake(self):
        self.assertEqual(_pascal_to_snake("IngestJobDefinition"), "ingest_job_definition")
        self.assertEqual(_pascal_to_snake("GdeltDiscovery"), "gdelt_discovery")
        self.assertEqual(_pascal_to_snake("S3RestoreOperator"), "s3_restore_operator")
        self.assertEqual(_pascal_to_snake("UrlFetchWorker"), "url_fetch_worker")


class RuntimeSourceBehaviorTests(unittest.TestCase):
    def test_gdelt_acquisition_source_is_skipped_by_generic_source_config(self):
        configs = build_source_configs(
            {
                "sources": {
                    "gdelt": {"enabled": True, "acquisition": {"max_files": 1}},
                    "iem_afos": {"enabled": True, "wfos": ["IND"], "product_types": ["AFD"]},
                }
            }
        )

        self.assertNotIn("gdelt", configs)
        self.assertIn("iem_afos", configs)

    def test_newsapi_fetch_fails_fast_without_api_key(self):
        source = NewsAPISource(api_key=None, query="grid")

        with self.assertRaisesRegex(EnvironmentError, "NEWSAPI_KEY"):
            source.fetch_page(page=1)

    def test_service_run_failure_detection_recurses_nested_results(self):
        self.assertTrue(
            _has_failed_result(
                {"service": "backfill", "results": {"newsapi": [{"status": "failed"}]}}
            )
        )
        self.assertFalse(
            _has_failed_result(
                {"service": "backfill", "results": {"iem_afos": [{"status": "success"}]}}
            )
        )


if __name__ == "__main__":
    unittest.main()
