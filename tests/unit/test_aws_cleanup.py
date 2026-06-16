import unittest

from eml_transformer.deployment.aws_cleanup import (
    build_cleanup_target,
    plan_stack_cleanup,
)


class AwsCleanupPlanTests(unittest.TestCase):
    def test_cleanup_target_is_derived_from_selected_deployment(self):
        target = build_cleanup_target(
            "configs/deployments/aws-dev.yaml",
            account_id="111122223333",
        )

        self.assertEqual(target.deployment, "aws-dev")
        self.assertEqual(target.stack_name, "eml-transformer-dev")
        self.assertEqual(target.data_bucket, "eml-transformer-dev-data-111122223333")
        self.assertIn("eml-transformer-dev-url-state", target.dynamodb_tables)
        self.assertIn("eml-transformer-dev-gdelt-discovery", target.batch_job_definition_names)

    def test_cleanup_plan_includes_destructive_resources(self):
        target = build_cleanup_target(
            "configs/deployments/aws-dev.yaml",
            account_id="111122223333",
        )
        actions = plan_stack_cleanup(target)
        action_pairs = {(action.action, action.service) for action in actions}

        self.assertIn(("delete_stack", "cloudformation"), action_pairs)
        self.assertIn(("empty_versioned_bucket", "s3"), action_pairs)
        self.assertIn(("delete_ecr_repository", "ecr"), action_pairs)
        self.assertIn(("delete_dynamodb_table", "dynamodb"), action_pairs)
        self.assertIn(("deregister_batch_job_definition", "batch"), action_pairs)


if __name__ == "__main__":
    unittest.main()
