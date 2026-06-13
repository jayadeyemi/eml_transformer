import sys
import unittest
import shutil
from pathlib import Path

if shutil.which("node") is None:
    App = None
    Template = None
else:
    try:
        from aws_cdk import App
        from aws_cdk.assertions import Template
    except ImportError:  # pragma: no cover - exercised in CI when CDK deps exist.
        App = None
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


if __name__ == "__main__":
    unittest.main()
