import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from eml_transformer.deployment.aws_validation import (
    build_validation_context,
    validate_container,
    validate_static,
)


class AwsValidationTests(unittest.TestCase):
    def test_context_uses_selected_deployment_and_action_artifact_root(self):
        context = build_validation_context("configs/deployments/aws-dev.yaml")

        self.assertEqual(context["deployment_name"], "aws-dev")
        self.assertEqual(context["deployment_config"], "configs/deployments/aws-dev.yaml")
        self.assertEqual(Path(context["results_dir"]).name, "aws_test_results")
        self.assertIn("artifacts", Path(context["results_dir"]).parts)

    def test_static_validation_dry_run_uses_direct_action_logs(self):
        with TemporaryDirectory() as tmpdir:
            result = validate_static(
                "configs/deployments/aws-dev.yaml",
                results_dir=tmpdir,
                dry_run=True,
            )

        self.assertTrue(result["ok"])
        log_paths = [command["log_path"] for command in result["commands"]]
        self.assertTrue(all("/static/" in path.replace("\\", "/") for path in log_paths))
        self.assertTrue(any("unit.log" in path for path in log_paths))
        self.assertTrue(any("contract.log" in path for path in log_paths))

    def test_container_validation_dry_run_targets_all_deployment_configs(self):
        with TemporaryDirectory() as tmpdir:
            result = validate_container(
                "configs/deployments/aws-dev.yaml",
                results_dir=tmpdir,
                dry_run=True,
            )

        args = [" ".join(command["argv"]) for command in result["commands"]]
        self.assertTrue(any("configs/deployments/aws-dev.yaml" in item for item in args))
        self.assertTrue(any("configs/deployments/aws-prod.yaml" in item for item in args))
        self.assertFalse(any("smoke default" in item.lower() for item in args))
        self.assertFalse(any("phase" in command["log_path"].lower() for command in result["commands"]))


if __name__ == "__main__":
    unittest.main()
