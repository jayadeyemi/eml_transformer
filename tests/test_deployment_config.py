import unittest
from unittest.mock import MagicMock

from eml_transformer.cli import _has_failed_result
from eml_transformer.cloud.aws.config import load_aws_runtime_config
from eml_transformer.deployment.config import (
    _pascal_to_snake,
    build_runtime_environment,
    deployment_config_warnings,
    deployment_matrix,
    load_deployment_config,
    render_runtime_config,
    render_runtime_config_from_cfn_outputs,
    validate_deployment_config,
)
from eml_transformer.ingestion.sources.newsapi import NewsAPISource
from eml_transformer.utils.config import build_source_configs


class DeploymentConfigTests(unittest.TestCase):
    def test_layered_aws_dev_config_validates_and_renders_runtime_contract(self):
        loaded = load_deployment_config("configs/deployments/aws-dev.yaml")
        errors = validate_deployment_config(loaded.config)
        runtime = render_runtime_config(loaded.config)
        env = build_runtime_environment(loaded.config)
        aws_config = load_aws_runtime_config(runtime)

        self.assertEqual(errors, [])
        self.assertEqual(loaded.config["infra"]["engine"], "cdk")
        self.assertIsNone(loaded.config["infra"]["account_id"])
        self.assertIn("weather_alerts", loaded.config["sources"])
        self.assertEqual(runtime["aws"]["infra_stack"], "eml-transformer-dev")
        self.assertEqual(env["INFRA_STACK"], "eml-transformer-dev")
        self.assertEqual(env["CDK_STACK"], "eml-transformer-dev")
        self.assertIn("BATCH_JOB_DEFINITION_GDELT_DISCOVERY", env)
        self.assertIn("BATCH_JOB_DEFINITION_S3_RESTORE_OPERATOR", env)
        self.assertEqual(aws_config.infra_stack, "eml-transformer-dev")
        self.assertEqual(aws_config.cdk_stack, "eml-transformer-dev")
        self.assertIn("batch_job_definitions", runtime["orchestration"])

    def test_deployment_matrix_includes_service_commands_contract(self):
        loaded = load_deployment_config("configs/deployments/aws-dev.yaml")
        matrix = deployment_matrix(loaded.config)
        services = {item["service"]: item for item in matrix["services"]}

        self.assertNotIn("estimated_monthly_cost", matrix)
        self.assertIn("url_fetch_worker", services)
        self.assertEqual(
            services["url_fetch_worker"]["job_definition_env_key"],
            "BATCH_JOB_DEFINITION_URL_FETCH_WORKER",
        )

    def test_smoke_config_renders_notifications_and_runtime_secrets_contract(self):
        loaded = load_deployment_config("configs/deployments/aws-smoke.yaml")
        errors = validate_deployment_config(loaded.config)
        runtime = render_runtime_config(loaded.config)
        env = build_runtime_environment(loaded.config)
        aws_config = load_aws_runtime_config(runtime)

        self.assertEqual(errors, [])
        self.assertEqual(
            runtime["notifications"]["sns_topic_arn"],
            "arn:aws:sns:us-east-1:123456789012:eml-transformer-smoke-notifications",
        )
        self.assertEqual(
            env["SNS_TOPIC_ARN"],
            "arn:aws:sns:us-east-1:123456789012:eml-transformer-smoke-notifications",
        )
        self.assertEqual(
            loaded.config["runtime_secrets"]["NEWSAPI_KEY"]["secret_arn_env"],
            "NEWSAPI_SECRET_ARN",
        )
        self.assertEqual(aws_config.sns_topic_arn, runtime["notifications"]["sns_topic_arn"])

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


class PlaceholderNetworkValidationTests(unittest.TestCase):
    """Phase 1: placeholder network values produce correct warnings / errors."""

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
        cfg = self._minimal_cdk_cfg()
        warnings = deployment_config_warnings(cfg)
        self.assertTrue(
            any("subnet_ids" in w for w in warnings),
            f"Expected subnet warning, got: {warnings}",
        )

    def test_placeholder_sgs_produce_warning_when_no_account_id(self):
        cfg = self._minimal_cdk_cfg()
        warnings = deployment_config_warnings(cfg)
        self.assertTrue(
            any("security_group_ids" in w for w in warnings),
            f"Expected sg warning, got: {warnings}",
        )

    def test_placeholder_subnets_produce_error_when_account_id_is_set(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["account_id"] = "123456789012"
        errors = validate_deployment_config(cfg)
        self.assertTrue(
            any("subnet_ids" in e for e in errors),
            f"Expected subnet error, got: {errors}",
        )

    def test_placeholder_sgs_produce_error_when_account_id_is_set(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["account_id"] = "123456789012"
        errors = validate_deployment_config(cfg)
        self.assertTrue(
            any("security_group_ids" in e for e in errors),
            f"Expected sg error, got: {errors}",
        )

    def test_real_subnet_and_sg_pass_validation(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["account_id"] = "123456789012"
        cfg["network"]["subnet_ids"] = ["subnet-abc12345"]
        cfg["network"]["security_group_ids"] = ["sg-abc12345"]
        errors = validate_deployment_config(cfg)
        network_errors = [e for e in errors if "subnet" in e or "security_group" in e]
        self.assertEqual(network_errors, [])

    def test_prod_without_account_id_produces_warning(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["environment"] = "prod"
        warnings = deployment_config_warnings(cfg)
        self.assertTrue(
            any("account_id" in w for w in warnings),
            f"Expected prod account_id warning, got: {warnings}",
        )

    def test_prod_with_account_id_and_no_alert_emails_is_error(self):
        cfg = self._minimal_cdk_cfg()
        cfg["infra"]["environment"] = "prod"
        cfg["infra"]["account_id"] = "123456789012"
        cfg["network"]["subnet_ids"] = ["subnet-abc12345"]
        cfg["network"]["security_group_ids"] = ["sg-abc12345"]
        errors = validate_deployment_config(cfg)
        self.assertTrue(
            any("alert_emails" in e for e in errors),
            f"Expected alert_emails error, got: {errors}",
        )

    def test_committed_aws_dev_config_still_passes_validate_all(self):
        """Placeholder subnets in committed configs (no account_id) must not error."""
        loaded = load_deployment_config("configs/deployments/aws-dev.yaml")
        errors = validate_deployment_config(loaded.config)
        self.assertEqual(errors, [])

    def test_committed_aws_prod_config_passes_validate_all(self):
        """aws-prod.yaml has no account_id so must not fail strict prod checks."""
        loaded = load_deployment_config("configs/deployments/aws-prod.yaml")
        errors = validate_deployment_config(loaded.config)
        self.assertEqual(errors, [])


class RenderFromCfnOutputsTests(unittest.TestCase):
    """Phase 2: render_runtime_config_from_cfn_outputs maps CF outputs correctly."""

    def _fake_cfn_client(self, outputs: list[dict]) -> MagicMock:
        client = MagicMock()
        client.describe_stacks.return_value = {
            "Stacks": [{"Outputs": outputs}]
        }
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
            "my-stack", "us-east-1",
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

        with self.assertRaises(ValueError, msg="Stack not found error expected"):
            render_runtime_config_from_cfn_outputs(
                "nonexistent-stack", _cfn_client=fake_client
            )

    def test_output_shape_matches_render_runtime_config_keys(self):
        """Keys returned must be loadable by load_aws_runtime_config."""
        import json

        runtime_env = {
            "AWS_REGION": "us-east-1",
            "EML_ENVIRONMENT": "dev",
            "INFRA_STACK": "s",
            "CDK_STACK": "s",
        }
        outputs = [
            {"OutputKey": "RuntimeEnvironment", "OutputValue": json.dumps(runtime_env)},
        ]

        result = render_runtime_config_from_cfn_outputs(
            "s", _cfn_client=self._fake_cfn_client(outputs)
        )

        # Must not raise — all keys should be present even if empty.
        from eml_transformer.cloud.aws.config import load_aws_runtime_config
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
                    "gdelt": {
                        "enabled": True,
                        "acquisition": {"max_files": 1},
                    },
                    "iem_afos": {
                        "enabled": True,
                        "wfos": ["IND"],
                        "product_types": ["AFD"],
                    },
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
                {
                    "service": "backfill",
                    "results": {
                        "newsapi": [{"status": "failed", "source": "newsapi"}],
                    },
                }
            )
        )
        self.assertFalse(
            _has_failed_result(
                {
                    "service": "backfill",
                    "results": {
                        "iem_afos": [{"status": "success", "source": "iem_afos"}],
                    },
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
