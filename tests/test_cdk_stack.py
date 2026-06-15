import os
import sys
import unittest
import shutil
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


class CdkStackTests(unittest.TestCase):
    @unittest.skipIf(App is None, "aws-cdk-lib or Node.js is not installed")
    def test_stack_synthesizes_core_collection_resources(self):
        repo_root = Path(__file__).resolve().parents[1]
        cdk_root = repo_root / "infra" / "cdk"
        sys.path.insert(0, str(cdk_root))

        from eml_transformer_cdk.stack import EmlTransformerCollectionStack

        loaded = load_deployment_config("configs/deployments/aws-dev.yaml")
        app = App()
        stack = EmlTransformerCollectionStack(
            app,
            "UnitTestStack",
            deployment_config=loaded.config,
        )
        template = Template.from_stack(stack)

        template.resource_count_is("AWS::S3::Bucket", 1)
        template.resource_count_is("AWS::SQS::Queue", 2)
        template.resource_count_is("AWS::DynamoDB::Table", 3)
        template.resource_count_is("AWS::Batch::JobDefinition", 8)
        # GDELT acquisition + generic source workflow + backfill workflow
        template.resource_count_is("AWS::StepFunctions::StateMachine", 3)

    @unittest.skipIf(App is None, "aws-cdk-lib or Node.js is not installed")
    def test_smoke_stack_synthesizes_sns_secrets_and_schedules(self):
        repo_root = Path(__file__).resolve().parents[1]
        cdk_root = repo_root / "infra" / "cdk"
        sys.path.insert(0, str(cdk_root))

        from eml_transformer_cdk.stack import EmlTransformerCollectionStack

        loaded = load_deployment_config("configs/deployments/aws-smoke.yaml")
        secret_arn = (
            "arn:aws:secretsmanager:us-east-1:123456789012:"
            "secret:newsapi-test"
        )

        with patch.dict(os.environ, {"NEWSAPI_SECRET_ARN": secret_arn}):
            app = App()
            stack = EmlTransformerCollectionStack(
                app,
                "SmokeUnitTestStack",
                deployment_config=loaded.config,
            )
            template = Template.from_stack(stack)

        template.resource_count_is("AWS::SNS::Topic", 1)
        template.resource_count_is("AWS::SNS::Subscription", 1)
        template.resource_count_is("AWS::Scheduler::Schedule", 2)
        template.has_resource_properties(
            "AWS::SNS::Subscription",
            {
                "Protocol": "email",
                "Endpoint": "boadeyem@iu.edu",
            },
        )
        template.has_resource_properties(
            "AWS::Batch::JobDefinition",
            {
                "ContainerProperties": Match.object_like(
                    {
                        "Secrets": Match.array_with(
                            [
                                {
                                    "Name": "NEWSAPI_KEY",
                                    "ValueFrom": secret_arn,
                                }
                            ]
                        )
                    }
                )
            },
        )
        template.has_resource_properties(
            "AWS::StepFunctions::StateMachine",
            {"DefinitionString": Match.string_like_regexp("sns:publish")},
        )
        template.has_resource_properties(
            "AWS::StepFunctions::StateMachine",
            {"DefinitionString": Match.string_like_regexp(r"ResultPath.*ingest_result")},
        )
        template.has_resource_properties(
            "AWS::StepFunctions::StateMachine",
            {"DefinitionString": Match.string_like_regexp(r"ResultPath.*standardize_result")},
        )
        template.has_resource_properties(
            "AWS::StepFunctions::StateMachine",
            {"DefinitionString": Match.string_like_regexp(r"ResultPath.*backfill_result")},
        )
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {"AlarmActions": Match.array_with([Match.any_value()])},
        )
        job_definitions = [
            resource["Properties"]
            for resource in template.to_json()["Resources"].values()
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
