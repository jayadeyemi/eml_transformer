import unittest
import tempfile
import zipfile
import gzip
import json
from types import SimpleNamespace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import pandas as pd

from eml_transformer import cli as cli_module
from eml_transformer.cloud.aws.config import AwsRuntimeConfig
from eml_transformer.cloud.aws.runtime import AwsAcquisitionRuntime
from eml_transformer.acquisition.gdelt.discovery import (
    GKG_COLUMNS,
    canonicalize_url,
    dataframe_to_url_records,
    discover_gdelt_file,
    filter_gkg,
    timestamps_for_day,
    url_hash,
)
from eml_transformer.storage.storage import LocalStorage


class ConditionalCheckFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamoDb:
    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item, ConditionExpression=None):
        key = Item["url_hash"]["S"]

        if ConditionExpression and key in self.items:
            raise ConditionalCheckFailed()

        self.items[key] = Item
        return {}


class FakeSqs:
    def __init__(self):
        self.entries = []
        self.messages = []
        self.deleted = []

    def send_message_batch(self, QueueUrl, Entries):
        self.entries.extend(Entries)
        return {"Successful": [{"Id": entry["Id"]} for entry in Entries]}

    def receive_message(
        self,
        QueueUrl,
        MaxNumberOfMessages,
        WaitTimeSeconds,
        VisibilityTimeout,
    ):
        messages = self.messages[:MaxNumberOfMessages]
        self.messages = self.messages[MaxNumberOfMessages:]
        return {"Messages": messages} if messages else {}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)

    def delete_message_batch(self, QueueUrl, Entries):
        for entry in Entries:
            self.deleted.append(entry["ReceiptHandle"])
        return {"Successful": [{"Id": e["Id"]} for e in Entries]}


class FakeStorage:
    def __init__(self):
        self.json_writes = {}

    def write_json(self, obj, key):
        self.json_writes[key] = obj


class FakeResponse:
    status_code = 200
    text = "<html><title>Storm</title><body><p>Power outage update.</p></body></html>"

    def raise_for_status(self):
        return None


class FakeBatch:
    def __init__(self):
        self.jobs = []

    def submit_job(self, **kwargs):
        self.jobs.append(kwargs)
        return {"jobId": "job-123"}


class FakeCloudWatch:
    def put_metric_data(self, **kwargs):
        return {}


class FakeS3:
    def __init__(self):
        self.restore_calls = []
        self.copy_calls = []
        self.head_calls = []

    def restore_object(self, **kwargs):
        self.restore_calls.append(kwargs)
        return {"ResponseMetadata": {"HTTPStatusCode": 202}}

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        return {
            "StorageClass": "DEEP_ARCHIVE",
            "Restore": 'ongoing-request="false"',
            "ContentLength": 10,
            "VersionId": "version-1",
        }

    def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)
        return {"VersionId": "version-2"}


class GdeltDiscoveryTests(unittest.TestCase):
    def test_timestamps_for_day_returns_96_quarter_hour_files(self):
        stamps = timestamps_for_day("2026-01-02")

        self.assertEqual(len(stamps), 96)
        self.assertEqual(stamps[0], "20260102000000")
        self.assertEqual(stamps[-1], "20260102234500")

    def test_canonicalize_url_removes_tracking_and_fragment(self):
        canonical = canonicalize_url(
            "HTTPS://www.Example.com/path/?utm_source=x&id=1#section"
        )

        self.assertEqual(canonical, "https://example.com/path?id=1")
        self.assertEqual(url_hash(canonical), url_hash(canonical + "#ignored"))

    def test_filter_and_url_records_dedupe_by_canonical_url(self):
        df = pd.DataFrame(
            [
                {
                    "GKGRECORDID": "1",
                    "DATE": "20260102000000",
                    "SourceCommonName": "example.com",
                    "DocumentIdentifier": "https://www.example.com/storm?id=1&utm_source=x",
                    "Themes": "NATURAL_DISASTER_SEVERE_WEATHER;",
                    "Locations": "1#United States#US#",
                    "Tone": "0",
                    "gdelt_timestamp": "20260102000000",
                    "gdelt_source_url": "http://data.gdeltproject.org/gdeltv2/20260102000000.gkg.csv.zip",
                },
                {
                    "GKGRECORDID": "2",
                    "DATE": "20260102001500",
                    "SourceCommonName": "example.com",
                    "DocumentIdentifier": "https://example.com/storm?id=1",
                    "Themes": "NATURAL_DISASTER_SEVERE_WEATHER;",
                    "Locations": "1#United States#US#",
                    "Tone": "0",
                    "gdelt_timestamp": "20260102001500",
                    "gdelt_source_url": "http://data.gdeltproject.org/gdeltv2/20260102001500.gkg.csv.zip",
                },
            ]
        )

        filtered = filter_gkg(df)
        rows = dataframe_to_url_records(filtered, run_id="run-1")

        self.assertEqual(len(filtered), 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["canonical_url"], "https://example.com/storm?id=1")

    def test_gdelt_file_discovery_uses_storage_cache(self):
        timestamp = "20260102000000"
        raw_bytes = self._gkg_zip_bytes(
            {
                "GKGRECORDID": "1",
                "DATE": timestamp,
                "SourceCommonName": "example.com",
                "DocumentIdentifier": "https://www.example.com/storm?id=1&utm_source=x",
                "Themes": "NATURAL_DISASTER_SEVERE_WEATHER;",
                "Locations": "1#United States#US#",
                "Tone": "0",
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_dir=Path(tmpdir))

            with patch(
                "eml_transformer.acquisition.gdelt.discovery.download_gkg_file",
                return_value=raw_bytes,
            ) as download:
                first = discover_gdelt_file(
                    timestamp=timestamp,
                    run_id="run-1",
                    storage=storage,
                )

            self.assertTrue(first.downloaded)
            self.assertFalse(first.parsed_from_cache)
            self.assertEqual(first.raw_rows, 1)
            self.assertEqual(len(first.urls), 1)
            self.assertTrue(storage.exists(first.raw_key))
            self.assertTrue(storage.exists(first.candidate_urls_key))
            download.assert_called_once()

            with patch(
                "eml_transformer.acquisition.gdelt.discovery.download_gkg_file",
                side_effect=AssertionError("cache should avoid download"),
            ):
                second = discover_gdelt_file(
                    timestamp=timestamp,
                    run_id="run-2",
                    storage=storage,
                )

            self.assertFalse(second.downloaded)
            self.assertTrue(second.parsed_from_cache)
            self.assertEqual(second.urls[0]["run_id"], "run-2")
            self.assertEqual(first.candidate_urls_key, second.candidate_urls_key)

    def _gkg_zip_bytes(self, overrides):
        row = {column: "" for column in GKG_COLUMNS}
        row.update(overrides)
        line = "\t".join(row[column] for column in GKG_COLUMNS)
        payload = BytesIO()

        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("sample.gkg.csv", line)

        return payload.getvalue()


class AwsRuntimeTests(unittest.TestCase):
    def test_enqueue_urls_skips_already_claimed_urls(self):
        dynamo = FakeDynamoDb()
        sqs = FakeSqs()
        runtime = AwsAcquisitionRuntime(
            config=AwsRuntimeConfig(
                region="us-east-1",
                environment="test",
                infra_stack="unit",
                url_fetch_queue_url="https://queue.example",
                url_state_table="url-state",
            ),
            storage=None,
            clients={"dynamodb": dynamo, "sqs": sqs},
        )
        url = {
            "run_id": "run-1",
            "canonical_url": "https://example.com/storm",
            "source_url": "https://example.com/storm",
            "source_domain": "example.com",
            "url_hash": url_hash("https://example.com/storm"),
        }

        self.assertEqual(runtime.enqueue_urls([url, dict(url)]), 1)
        self.assertEqual(len(sqs.entries), 1)

    def test_gdelt_max_urls_per_run_caps_queued_urls(self):
        dynamo = FakeDynamoDb()
        sqs = FakeSqs()
        storage = FakeStorage()
        runtime = AwsAcquisitionRuntime(
            config=AwsRuntimeConfig(
                region="us-east-1",
                environment="test",
                infra_stack="unit",
                url_fetch_queue_url="https://queue.example",
                url_state_table="url-state",
                gdelt_max_urls_per_run=1,
            ),
            storage=storage,
            clients={"dynamodb": dynamo, "sqs": sqs},
        )
        urls = [
            {
                "run_id": "run-1",
                "canonical_url": "https://example.com/storm-1",
                "source_url": "https://example.com/storm-1",
                "source_domain": "example.com",
                "url_hash": url_hash("https://example.com/storm-1"),
            },
            {
                "run_id": "run-1",
                "canonical_url": "https://example.com/storm-2",
                "source_url": "https://example.com/storm-2",
                "source_domain": "example.com",
                "url_hash": url_hash("https://example.com/storm-2"),
            },
        ]
        file_result = SimpleNamespace(
            error=None,
            timestamp="20260101000000",
            raw_key="raw",
            candidate_urls_key="candidate",
            manifest_key="manifest",
            raw_rows=2,
            filtered_rows=2,
            urls=urls,
            downloaded=True,
            parsed_from_cache=False,
            raw_content_hash="hash",
            raw_size_bytes=123,
        )

        with patch(
            "eml_transformer.cloud.aws.runtime.iter_gdelt_file_discoveries",
            return_value=[file_result],
        ):
            result = runtime.discover_and_enqueue(date="2026-01-01", run_id="run-1")

        self.assertEqual(result["max_urls_per_run"], 1)
        self.assertEqual(result["urls_discovered"], 2)
        self.assertEqual(result["urls_queued"], 1)
        self.assertEqual(len(sqs.entries), 1)

    def test_start_service_submits_generic_batch_command(self):
        batch = FakeBatch()
        runtime = AwsAcquisitionRuntime(
            config=AwsRuntimeConfig(
                region="us-east-1",
                environment="test",
                infra_stack="unit",
                batch_job_queue="queue-arn",
                batch_job_definitions={
                    "ingest": "job-definition-arn",
                },
            ),
            storage=None,
            clients={"batch": batch},
            service_config_path="configs/generated/aws-dev.runtime.yaml",
        )

        result = runtime.start_service(
            service="ingest",
            run_id="run-1",
            parameters={"source": "weather_alerts"},
        )

        self.assertEqual(result["mode"], "batch")
        self.assertEqual(batch.jobs[0]["jobDefinition"], "job-definition-arn")
        self.assertEqual(
            batch.jobs[0]["containerOverrides"]["command"],
            [
                "ingest",
                "--config",
                "configs/generated/aws-dev.runtime.yaml",
                "--source",
                "weather_alerts",
            ],
        )

    def test_service_job_definition_preferred_over_generic(self):
        config = AwsRuntimeConfig(
            region="us-east-1",
            environment="test",
            infra_stack="unit",
            batch_job_definitions={
                "ingest": "ingest-job-definition",
            },
        )

        self.assertEqual(
            config.job_definition_for("ingest"),
            "ingest-job-definition",
        )

    def test_missing_job_definition_returns_none(self):
        config = AwsRuntimeConfig(
            region="us-east-1",
            environment="test",
            infra_stack="unit",
            batch_job_definitions={},
        )

        self.assertIsNone(config.job_definition_for("ingest"))

    def test_restore_s3_object_requests_deep_archive_restore(self):
        s3 = FakeS3()
        runtime = AwsAcquisitionRuntime(
            config=AwsRuntimeConfig(
                region="us-east-1",
                environment="test",
                infra_stack="unit",
            ),
            storage=None,
            clients={"s3": s3},
        )

        result = runtime.restore_s3_object(
            key="bronze/gdelt/raw/file.zip",
            bucket="bucket-1",
            days=7,
            tier="Bulk",
            run_id="run-1",
        )

        self.assertEqual(result["status"], "restore_requested")
        self.assertEqual(s3.restore_calls[0]["Bucket"], "bucket-1")
        self.assertEqual(s3.restore_calls[0]["Key"], "bronze/gdelt/raw/file.zip")
        self.assertEqual(
            s3.restore_calls[0]["RestoreRequest"],
            {
                "Days": 7,
                "GlacierJobParameters": {"Tier": "Bulk"},
            },
        )

    def test_rehydrate_s3_object_copies_with_runtime_tags(self):
        s3 = FakeS3()
        runtime = AwsAcquisitionRuntime(
            config=AwsRuntimeConfig(
                region="us-east-1",
                environment="test",
                infra_stack="unit",
            ),
            storage=None,
            clients={"s3": s3},
        )

        result = runtime.rehydrate_s3_object(
            key="bronze/gdelt/raw/file.zip",
            destination_key="restore-staging/bronze/gdelt/raw/file.zip",
            bucket="bucket-1",
            version_id="version-1",
            run_id="run-1",
        )

        call = s3.copy_calls[0]
        tags = parse_qs(call["Tagging"])

        self.assertEqual(result["destination_version_id"], "version-2")
        self.assertEqual(call["StorageClass"], "STANDARD")
        self.assertEqual(
            call["CopySource"],
            {
                "Bucket": "bucket-1",
                "Key": "bronze/gdelt/raw/file.zip",
                "VersionId": "version-1",
            },
        )
        self.assertEqual(call["Key"], "restore-staging/bronze/gdelt/raw/file.zip")
        self.assertEqual(tags["project"], ["eml_transformer"])
        self.assertEqual(tags["environment"], ["test"])
        self.assertEqual(tags["infra_stack"], ["unit"])
        self.assertEqual(tags["run_id"], ["run-1"])
        self.assertEqual(tags["source"], ["s3_restore"])

    def test_fetch_articles_can_write_compressed_jsonl_batches(self):
        sqs = FakeSqs()
        messages = []

        for idx in range(2):
            body = {
                "run_id": "run-1",
                "canonical_url": f"https://example.com/article-{idx}",
                "source_url": f"https://example.com/article-{idx}",
                "source": "gdelt",
                "source_domain": "example.com",
                "url_hash": url_hash(f"https://example.com/article-{idx}"),
            }
            messages.append(
                {
                    "Body": json.dumps(body),
                    "ReceiptHandle": f"receipt-{idx}",
                }
            )

        sqs.messages = messages

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = AwsAcquisitionRuntime(
                config=AwsRuntimeConfig(
                    region="us-east-1",
                    environment="test",
                    infra_stack="unit",
                    url_fetch_queue_url="https://queue.example",
                ),
                storage=LocalStorage(base_dir=Path(tmpdir)),
                clients={"sqs": sqs, "cloudwatch": FakeCloudWatch()},
            )

            with patch(
                "eml_transformer.cloud.aws.runtime.requests.get",
                return_value=FakeResponse(),
            ):
                result = runtime.fetch_articles(
                    run_id="run-1",
                    max_messages=2,
                    wait_time_seconds=0,
                    output_batch_size=2,
                    output_format="jsonl.gz",
                )

            batch_keys = runtime.storage.list("bronze/articles/batches/")
            batch_payload = runtime.storage.read_bytes(batch_keys[0])
            rows = [
                json.loads(line)
                for line in gzip.decompress(batch_payload).decode("utf-8").splitlines()
            ]

        self.assertEqual(result.fetched, 2)
        self.assertEqual(sqs.deleted, ["receipt-0", "receipt-1"])
        self.assertEqual(len(batch_keys), 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "article_fetch")


    def test_fetch_articles_skips_throttled_domain_without_deleting(self):
        """Messages from a throttled domain must not be deleted from SQS."""
        sqs = FakeSqs()

        class ThrottledDynamoDb:
            """Always rejects the domain throttle claim (simulates active throttle)."""
            class _Error(Exception):
                response = {"Error": {"Code": "ConditionalCheckFailedException"}}

            def put_item(self, **kwargs):
                raise self._Error()

        messages = [
            {
                "Body": json.dumps({
                    "run_id": "run-1",
                    "canonical_url": "https://example.com/article-throttled",
                    "source_url": "https://example.com/article-throttled",
                    "source": "gdelt",
                    "source_domain": "example.com",
                    "url_hash": url_hash("https://example.com/article-throttled"),
                }),
                "ReceiptHandle": "receipt-throttled",
            }
        ]
        sqs.messages = list(messages)

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = AwsAcquisitionRuntime(
                config=AwsRuntimeConfig(
                    region="us-east-1",
                    environment="test",
                    infra_stack="unit",
                    url_fetch_queue_url="https://queue.example",
                    domain_throttle_table="domain-throttle",
                ),
                storage=LocalStorage(base_dir=Path(tmpdir)),
                clients={
                    "sqs": sqs,
                    "dynamodb": ThrottledDynamoDb(),
                    "cloudwatch": FakeCloudWatch(),
                },
            )
            result = runtime.fetch_articles(
                run_id="run-1",
                max_messages=1,
                wait_time_seconds=0,
                request_delay_seconds=1.0,
            )

        # Message should be throttled: not fetched, not deleted, counted as throttled.
        self.assertEqual(result.received, 0)
        self.assertEqual(result.fetched, 0)
        self.assertEqual(result.throttled, 1)
        self.assertEqual(sqs.deleted, [])

    def test_fetch_articles_uses_batch_delete_for_single_object_path(self):
        """Single-object path must use delete_message_batch, not delete_message."""
        sqs = FakeSqs()

        for idx in range(2):
            body = {
                "run_id": "run-1",
                "canonical_url": f"https://example.com/article-{idx}",
                "source_url": f"https://example.com/article-{idx}",
                "source": "gdelt",
                "source_domain": "example.com",
                "url_hash": url_hash(f"https://example.com/article-{idx}"),
            }
            sqs.messages.append(
                {"Body": json.dumps(body), "ReceiptHandle": f"receipt-{idx}"}
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = AwsAcquisitionRuntime(
                config=AwsRuntimeConfig(
                    region="us-east-1",
                    environment="test",
                    infra_stack="unit",
                    url_fetch_queue_url="https://queue.example",
                ),
                storage=LocalStorage(base_dir=Path(tmpdir)),
                clients={"sqs": sqs, "cloudwatch": FakeCloudWatch()},
            )

            with patch(
                "eml_transformer.cloud.aws.runtime.requests.get",
                return_value=FakeResponse(),
            ):
                result = runtime.fetch_articles(
                    run_id="run-1",
                    max_messages=2,
                    wait_time_seconds=0,
                    output_batch_size=1,
                    output_format="json",
                )

        self.assertEqual(result.fetched, 2)
        self.assertIn("receipt-0", sqs.deleted)
        self.assertIn("receipt-1", sqs.deleted)


if __name__ == "__main__":
    unittest.main()
