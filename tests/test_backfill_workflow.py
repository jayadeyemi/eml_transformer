import unittest
from datetime import date
from unittest.mock import patch

from eml_transformer.cloud.aws.config import AwsRuntimeConfig
from eml_transformer.cloud.aws.runtime import AwsAcquisitionRuntime
from eml_transformer.pipelines.backfill_pipeline import BackfillPipeline
from eml_transformer.services.collection import CollectionServiceRunner


class FakeSource:
    update_mode = "incremental"
    supports_backfill = True


class FakeResult:
    status = "success"

    def __init__(self, source: str, from_date: str, to_date: str):
        self.source = source
        self.from_date = from_date
        self.to_date = to_date

    def to_summary(self):
        return {
            "status": self.status,
            "source": self.source,
            "from": self.from_date,
            "to": self.to_date,
        }


class FakeIngestionPipeline:
    def __init__(self):
        self.calls = []
        self.seeded = []

    def run_source(
        self,
        source_name,
        source_kwargs,
        from_date=None,
        to_date=None,
        update_checkpoint=True,
    ):
        self.calls.append((source_name, from_date, to_date, update_checkpoint))
        return FakeResult(source_name, from_date, to_date)

    def initialize_checkpoint(self, source_name, checkpoint_value, run_id):
        self.seeded.append((source_name, checkpoint_value, run_id))


class FakeStepFunctions:
    def __init__(self):
        self.calls = []

    def start_execution(self, **kwargs):
        self.calls.append(kwargs)
        return {"executionArn": "arn:aws:states:us-east-1:123:execution:backfill/test"}


class BackfillWorkflowTests(unittest.TestCase):
    def test_backfill_windows_validate_dates_and_window_size(self):
        with self.assertRaisesRegex(ValueError, "window_days"):
            list(
                BackfillPipeline._iter_date_windows(
                    date(2026, 1, 1),
                    date(2026, 1, 2),
                    0,
                )
            )

        with self.assertRaisesRegex(ValueError, "start"):
            list(
                BackfillPipeline._iter_date_windows(
                    date(2026, 1, 2),
                    date(2026, 1, 1),
                    1,
                )
            )

    def test_run_all_backfills_all_supported_sources_and_seeds_checkpoint(self):
        ingestion = FakeIngestionPipeline()
        pipeline = BackfillPipeline(ingestion_pipeline=ingestion)

        with patch(
            "eml_transformer.pipelines.backfill_pipeline.create_source",
            return_value=FakeSource(),
        ):
            results = pipeline.run_all(
                {"newsapi": {}, "iem_afos": {}},
                start_date="2026-01-01",
                end_date="2026-01-03",
                window_days=2,
                seed_checkpoint=True,
            )

        self.assertEqual(sorted(results), ["iem_afos", "newsapi"])
        self.assertEqual(len(results["newsapi"]), 2)
        self.assertIn(("newsapi", "2026-01-03", "backfill_seed"), ingestion.seeded)
        self.assertIn(("iem_afos", "2026-01-03", "backfill_seed"), ingestion.seeded)

    def test_collection_service_summarizes_source_all_backfill_results(self):
        runner = CollectionServiceRunner.__new__(CollectionServiceRunner)
        rows = runner._summarize_results(
            {
                "newsapi": [FakeResult("newsapi", "2026-01-01", "2026-01-02")],
                "iem_afos": [FakeResult("iem_afos", "2026-01-01", "2026-01-02")],
            }
        )

        self.assertEqual(
            [row["source"] for row in rows],
            ["newsapi", "iem_afos"],
        )

    def test_backfill_batch_command_includes_runtime_knobs(self):
        runtime = AwsAcquisitionRuntime(
            config=AwsRuntimeConfig(
                region="us-east-1",
                environment="dev",
                batch_job_queue="queue",
                batch_job_definitions={"backfill": "jobdef"},
            ),
            storage=object(),
        )

        command = runtime.build_service_command(
            "backfill",
            {
                "source": "all",
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "window_days": 7,
                "init_checkpoint": True,
            },
        )

        self.assertIn("--window-days", command)
        self.assertIn("7", command)
        self.assertIn("--init-checkpoint", command)

    def test_backfill_state_machine_uses_backfill_workflow_arn(self):
        stepfunctions = FakeStepFunctions()
        runtime = AwsAcquisitionRuntime(
            config=AwsRuntimeConfig(
                region="us-east-1",
                environment="dev",
                backfill_workflow_arn="arn:aws:states:us-east-1:123:stateMachine:backfill",
            ),
            storage=object(),
            clients={"stepfunctions": stepfunctions},
        )

        result = runtime.start_service(
            "backfill",
            parameters={
                "source": "all",
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "window_days": 7,
            },
            run_id="test-run",
            use_state_machine=True,
        )

        self.assertEqual(result["mode"], "stepfunctions")
        self.assertEqual(
            stepfunctions.calls[0]["stateMachineArn"],
            "arn:aws:states:us-east-1:123:stateMachine:backfill",
        )


if __name__ == "__main__":
    unittest.main()
