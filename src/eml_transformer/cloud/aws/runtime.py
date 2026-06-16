from __future__ import annotations

import json
import time
import gzip
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from eml_transformer.cloud.aws.config import AwsRuntimeConfig, normalize_service_name
from eml_transformer.acquisition.gdelt.discovery import (
    clean_domain,
    iter_gdelt_file_discoveries,
    resolve_gdelt_date,
)
from eml_transformer.storage.storage import Storage


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_boto3():
    try:
        import boto3
    except ImportError as exc:
        raise ImportError(
            "AWS runtime commands require boto3. Install dependencies with "
            "`python -m pip install -e .` or `python -m pip install boto3`."
        ) from exc

    return boto3


@dataclass
class ArticleFetchResult:
    received: int = 0
    fetched: int = 0
    failed: int = 0
    throttled: int = 0


class AwsAcquisitionRuntime:
    def __init__(
        self,
        config: AwsRuntimeConfig,
        storage: Storage,
        clients: dict[str, Any] | None = None,
        service_config_path: str = "configs/local.yaml",
    ):
        self.config = config
        self.storage = storage
        self.service_config_path = service_config_path
        self._clients = clients or {}
        self._session: Any = None  # lazy boto3.Session

    def _boto3_session(self) -> Any:
        """Return a cached boto3 Session, respecting aws_profile when set."""
        if self._session is None:
            boto3 = _require_boto3()
            self._session = boto3.Session(
                profile_name=self.config.aws_profile or None,
                region_name=self.config.region,
            )
        return self._session

    def _client(self, service: str) -> Any:
        if service not in self._clients:
            self._clients[service] = self._boto3_session().client(service)
        return self._clients[service]

    def discover_and_enqueue(
        self,
        date: str,
        run_id: str | None = None,
        max_files: int | None = None,
        max_urls: int | None = None,
        enqueue: bool = True,
    ) -> dict[str, Any]:
        run_id = run_id or utc_run_id()
        max_urls_per_run = (
            max_urls
            if max_urls is not None
            else self.config.gdelt_max_urls_per_run
        )
        self.record_run(
            run_id=run_id,
            job_type="gdelt_discovery",
            status="running",
            payload={
                "date": date,
                "max_files": max_files,
                "max_urls_per_run": max_urls_per_run,
            },
        )

        resolved_date = resolve_gdelt_date(date)
        manifest_key = self._manifest_key(run_id=run_id, name="gdelt_discovery")
        raw_rows = 0
        filtered_rows = 0
        urls_discovered = 0
        queued = 0
        failures = []
        file_outputs = []

        for file_result in iter_gdelt_file_discoveries(
            date=resolved_date,
            run_id=run_id,
            storage=self.storage,
            max_files=max_files,
        ):
            if file_result.error:
                failures.append(
                    {
                        "timestamp": file_result.timestamp,
                        "error": file_result.error,
                    }
                )
                continue

            raw_rows += file_result.raw_rows
            filtered_rows += file_result.filtered_rows
            urls_discovered += len(file_result.urls)
            urls_to_enqueue = file_result.urls

            if max_urls_per_run is not None:
                remaining = max(max_urls_per_run - queued, 0)
                urls_to_enqueue = urls_to_enqueue[:remaining]

            if enqueue and urls_to_enqueue:
                queued += self.enqueue_urls(urls_to_enqueue)

            file_outputs.append(
                {
                    "timestamp": file_result.timestamp,
                    "raw_key": file_result.raw_key,
                    "candidate_urls_key": file_result.candidate_urls_key,
                    "manifest_key": file_result.manifest_key,
                    "raw_rows": file_result.raw_rows,
                    "filtered_rows": file_result.filtered_rows,
                    "urls_discovered": len(file_result.urls),
                    "urls_queued": len(urls_to_enqueue) if enqueue else 0,
                    "downloaded": file_result.downloaded,
                    "parsed_from_cache": file_result.parsed_from_cache,
                    "raw_content_hash": file_result.raw_content_hash,
                    "raw_size_bytes": file_result.raw_size_bytes,
                }
            )

            if (
                enqueue
                and max_urls_per_run is not None
                and queued >= max_urls_per_run
            ):
                break

        self.storage.write_json(
            {
                "run_id": run_id,
                "source": "gdelt",
                "date": resolved_date,
                "raw_rows": raw_rows,
                "filtered_rows": filtered_rows,
                "urls_discovered": urls_discovered,
                "urls_queued": queued,
                "max_urls_per_run": max_urls_per_run,
                "file_outputs": file_outputs,
                "failures": failures,
                "tags": self._tags(run_id, source="gdelt"),
            },
            manifest_key,
        )

        self.put_metric("GdeltRawRows", raw_rows, source="gdelt")
        self.put_metric("GdeltFilteredRows", filtered_rows, source="gdelt")
        self.put_metric("UrlsDiscovered", urls_discovered, source="gdelt")
        self.put_metric("UrlsQueued", queued, source="gdelt")
        self.record_run(
            run_id=run_id,
            job_type="gdelt_discovery",
            status="success",
            payload={
                "date": resolved_date,
                "raw_rows": raw_rows,
                "filtered_rows": filtered_rows,
                "urls_discovered": urls_discovered,
                "urls_queued": queued,
                "max_urls_per_run": max_urls_per_run,
                "manifest_key": manifest_key,
                "file_outputs": file_outputs,
                "failures": failures,
            },
        )

        return {
            "run_id": run_id,
            "source": "gdelt",
            "raw_rows": raw_rows,
            "filtered_rows": filtered_rows,
            "urls_discovered": urls_discovered,
            "urls_queued": queued,
            "max_urls_per_run": max_urls_per_run,
            "manifest_key": manifest_key,
            "file_outputs": file_outputs,
            "failures": failures,
        }

    def enqueue_from_key(self, key: str) -> int:
        if key.endswith(".jsonl"):
            urls = self.storage.read_jsonl(key)
        else:
            payload = self.storage.read_json(key)
            urls = (
                payload["urls"]
                if isinstance(payload, dict) and "urls" in payload
                else payload
            )

        if not isinstance(urls, list):
            raise TypeError(f"Expected list of URL records in {key}")

        queued = self.enqueue_urls(urls)
        self.put_metric("UrlsQueued", queued, source="url_fetch")
        return queued

    def enqueue_urls(self, urls: list[dict[str, Any]]) -> int:
        if not self.config.url_fetch_queue_url:
            raise ValueError(
                "Missing queues.url_fetch_queue_url or URL_FETCH_QUEUE_URL"
            )

        queue_url = self.config.url_fetch_queue_url
        sqs = self._client("sqs")
        queued = 0
        batch: list[dict[str, str]] = []

        for row in urls:
            if self._claim_url(row):
                batch.append(
                    {
                        "Id": row["url_hash"][:80],
                        "MessageBody": json.dumps(
                            {
                                **row,
                                "tags": self._tags(
                                    row.get("run_id"),
                                    source=row.get("source", "url_fetch"),
                                ),
                                "queued_at": utc_iso(),
                            },
                            default=str,
                        ),
                    }
                )

            if len(batch) == 10:
                queued += self._send_sqs_batch(sqs, queue_url, batch)
                batch = []

        if batch:
            queued += self._send_sqs_batch(sqs, queue_url, batch)

        return queued

    def fetch_articles(
        self,
        run_id: str | None = None,
        max_messages: int = 10,
        wait_time_seconds: int = 10,
        visibility_timeout: int = 120,
        request_delay_seconds: float = 0.0,
        output_batch_size: int = 1,
        output_format: str = "json",
    ) -> ArticleFetchResult:
        if not self.config.url_fetch_queue_url:
            raise ValueError(
                "Missing queues.url_fetch_queue_url or URL_FETCH_QUEUE_URL"
            )

        run_id = run_id or utc_run_id()
        sqs = self._client("sqs")
        result = ArticleFetchResult()
        pending_batch: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        use_batched_output = (
            output_batch_size > 1 and output_format.lower() in {"jsonl.gz", "jsonl"}
        )
        # Safety limit: stop polling after seeing 3× max_messages to prevent
        # an infinite loop when all messages are domain-throttled.
        max_seen = max(max_messages * 3, 30)
        total_seen = 0

        while result.received < max_messages and total_seen < max_seen:
            response = sqs.receive_message(
                QueueUrl=self.config.url_fetch_queue_url,
                MaxNumberOfMessages=min(10, max_messages - result.received),
                WaitTimeSeconds=wait_time_seconds,
                VisibilityTimeout=visibility_timeout,
            )
            messages = response.get("Messages", [])

            if not messages:
                break

            # Accumulate receipt handles for single-object path batch delete.
            single_delete_entries: list[dict[str, str]] = []

            for msg_idx, message in enumerate(messages):
                total_seen += 1
                body = json.loads(message["Body"])

                # Per-domain throttle check using DynamoDB.  If the domain
                # was fetched within the configured delay window, leave the
                # message in-flight (visibility timeout will return it to the
                # queue) and move on.
                url = body.get("canonical_url", "")
                domain = clean_domain(url) or "unknown"

                if not self._try_claim_domain_throttle(domain, request_delay_seconds):
                    result.throttled += 1
                    continue

                result.received += 1

                try:
                    payload = self._fetch_article_payload(body=body, run_id=run_id)

                    if use_batched_output:
                        pending_batch.append((message, body, payload))

                        if len(pending_batch) >= output_batch_size:
                            result.fetched += self._flush_article_batch(
                                pending_batch,
                                sqs=sqs,
                                run_id=run_id,
                                output_format=output_format,
                            )
                            pending_batch = []
                    else:
                        article_key = self._write_article_payload(payload)
                        self._update_url_state(
                            body,
                            {
                                "status": "fetched",
                                "article_storage_path": article_key,
                                "last_attempt_at": utc_iso(),
                            },
                        )
                        single_delete_entries.append(
                            {
                                "Id": str(msg_idx),
                                "ReceiptHandle": message["ReceiptHandle"],
                            }
                        )
                        result.fetched += 1
                except Exception as exc:
                    self._update_url_state(
                        body,
                        {
                            "status": "failed",
                            "last_attempt_at": utc_iso(),
                            "error_message": str(exc)[:1000],
                        },
                    )
                    result.failed += 1

                if request_delay_seconds > 0:
                    time.sleep(request_delay_seconds)

            # Batch-delete successfully fetched messages in the single-object path.
            if single_delete_entries:
                self._sqs_delete_batch(sqs, single_delete_entries)

        if pending_batch:
            result.fetched += self._flush_article_batch(
                pending_batch,
                sqs=sqs,
                run_id=run_id,
                output_format=output_format,
            )

        self.put_metric("UrlFetchMessagesReceived", result.received, source="url_fetch")
        self.put_metric("UrlFetchSucceeded", result.fetched, source="url_fetch")
        self.put_metric("UrlFetchFailed", result.failed, source="url_fetch")
        self.record_run(
            run_id=run_id,
            job_type="url_fetch_worker",
            status="success",
            payload=result.__dict__,
        )
        return result

    def start_service(
        self,
        service: str,
        parameters: dict[str, Any] | None = None,
        run_id: str | None = None,
        use_state_machine: bool = False,
    ) -> dict[str, Any]:
        service = normalize_service_name(service)
        run_id = run_id or utc_run_id()
        parameters = {k: v for k, v in (parameters or {}).items() if v is not None}

        if service == "backfill":
            parameters.setdefault("source", "all")
            parameters.setdefault("window_days", 30)
            parameters.setdefault("init_checkpoint", False)
            self._require_parameters(
                parameters,
                service="backfill",
                required=("source", "start_date", "end_date"),
            )

        payload = {
            "service": service,
            "run_id": run_id,
            "parameters": parameters,
            "tags": self._tags(run_id, source=parameters.get("source", service)),
        }

        if use_state_machine:
            workflow_arn = self._workflow_arn_for_service(service)

            response = self._client("stepfunctions").start_execution(
                stateMachineArn=workflow_arn,
                name=f"{service}-{run_id}",
                input=json.dumps(payload),
            )
            self.record_run(
                run_id=run_id,
                job_type=f"start_{service}",
                status="submitted",
                payload={"mode": "stepfunctions", **payload},
            )
            return {
                "mode": "stepfunctions",
                "service": service,
                "run_id": run_id,
                "execution_arn": response["executionArn"],
            }

        if not self.config.batch_job_queue:
            raise ValueError("Missing orchestration.batch_job_queue or BATCH_JOB_QUEUE")

        job_definition = self.config.job_definition_for(service)

        if not job_definition:
            raise ValueError(
                "Missing batch job definition for service "
                f"{service!r}. Configure orchestration.batch_job_definitions."
            )

        command = self.build_service_command(service, parameters)
        response = self._client("batch").submit_job(
            jobName=f"{service}-{run_id}",
            jobQueue=self.config.batch_job_queue,
            jobDefinition=job_definition,
            parameters={k: str(v) for k, v in parameters.items()},
            containerOverrides={"command": command},
            tags=self._tags(run_id, source=parameters.get("source", service)),
        )
        self.record_run(
            run_id=run_id,
            job_type=f"start_{service}",
            status="submitted",
            payload={
                "mode": "batch",
                "service": service,
                "job_definition": job_definition,
                "parameters": parameters,
                "command": command,
                "job_id": response["jobId"],
            },
        )
        return {
            "mode": "batch",
            "service": service,
            "run_id": run_id,
            "job_id": response["jobId"],
            "command": command,
        }

    def start_ingestion(
        self,
        date: str,
        run_id: str | None = None,
        max_files: int | None = None,
    ) -> dict[str, Any]:
        return self.start_service(
            service="gdelt_discovery",
            run_id=run_id,
            parameters={"date": date, "max_files": max_files},
            use_state_machine=bool(self.config.state_machine_arn),
        )

    def restore_s3_object(
        self,
        key: str,
        bucket: str | None = None,
        version_id: str | None = None,
        days: int = 7,
        tier: str = "Bulk",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or utc_run_id()
        bucket, object_key = self._resolve_s3_object(key=key, bucket=bucket)
        restore_request = {
            "Days": days,
            "GlacierJobParameters": {
                "Tier": self._normalize_restore_tier(tier),
            },
        }
        params = {
            "Bucket": bucket,
            "Key": object_key,
            "RestoreRequest": restore_request,
        }

        if version_id:
            params["VersionId"] = version_id

        status = "restore_requested"
        http_status = None

        try:
            response = self._client("s3").restore_object(**params)
            http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")

            if error_code == "RestoreAlreadyInProgress":
                status = "restore_in_progress"
            elif error_code == "ObjectAlreadyInActiveTierError":
                status = "already_active"
            else:
                raise

        result = {
            "run_id": run_id,
            "status": status,
            "bucket": bucket,
            "key": object_key,
            "version_id": version_id,
            "days": days,
            "tier": self._normalize_restore_tier(tier),
            "http_status": http_status,
            "tags": self._tags(run_id, source="s3_restore"),
        }
        self.record_run(
            run_id=run_id,
            job_type="s3_restore_object",
            status=status,
            payload=result,
        )
        return result

    def s3_object_restore_status(
        self,
        key: str,
        bucket: str | None = None,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        bucket, object_key = self._resolve_s3_object(key=key, bucket=bucket)
        params = {
            "Bucket": bucket,
            "Key": object_key,
        }

        if version_id:
            params["VersionId"] = version_id

        response = self._client("s3").head_object(**params)
        return {
            "bucket": bucket,
            "key": object_key,
            "version_id": response.get("VersionId") or version_id,
            "storage_class": response.get("StorageClass", "STANDARD"),
            "restore": response.get("Restore"),
            "content_length": response.get("ContentLength"),
            "last_modified": response.get("LastModified"),
        }

    def rehydrate_s3_object(
        self,
        key: str,
        destination_key: str | None = None,
        bucket: str | None = None,
        version_id: str | None = None,
        storage_class: str = "STANDARD",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or utc_run_id()
        bucket, source_key = self._resolve_s3_object(key=key, bucket=bucket)
        _, target_key = self._resolve_s3_object(
            key=destination_key or key,
            bucket=bucket,
        )
        copy_source: dict[str, Any] = {
            "Bucket": bucket,
            "Key": source_key,
        }

        if version_id:
            copy_source["VersionId"] = version_id

        tags = self._tags(run_id, source="s3_restore")
        response = self._client("s3").copy_object(
            Bucket=bucket,
            Key=target_key,
            CopySource=copy_source,
            StorageClass=self._normalize_storage_class(storage_class),
            MetadataDirective="COPY",
            TaggingDirective="REPLACE",
            Tagging=urlencode(tags),
        )
        result = {
            "run_id": run_id,
            "bucket": bucket,
            "source_key": source_key,
            "destination_key": target_key,
            "source_version_id": version_id,
            "destination_version_id": response.get("VersionId"),
            "storage_class": self._normalize_storage_class(storage_class),
            "tags": tags,
        }
        self.record_run(
            run_id=run_id,
            job_type="s3_rehydrate_object",
            status="success",
            payload=result,
        )
        return result

    def build_service_command(
        self,
        service: str,
        parameters: dict[str, Any],
    ) -> list[str]:
        config_args = ["--config", self.service_config_path]

        if service == "ingest":
            return ["ingest", *config_args, "--source", parameters.get("source", "all")]

        if service == "standardize":
            return [
                "standardize",
                *config_args,
                "--source",
                parameters.get("source", "all"),
            ]

        if service == "embed":
            command = ["embed", *config_args, "--source", parameters.get("source", "all")]
            model_name = parameters.get("model_name")

            if model_name:
                command.extend(["--model", str(model_name)])

            return command

        if service == "run_all":
            return ["run-all", *config_args]

        if service == "backfill":
            self._require_parameters(
                parameters,
                service="backfill",
                required=("source", "start_date", "end_date"),
            )
            command = [
                "backfill",
                *config_args,
                "--source",
                parameters["source"],
                "--start-date",
                parameters["start_date"],
                "--end-date",
                parameters["end_date"],
                "--window-days",
                str(parameters.get("window_days", 30)),
            ]

            if self._as_bool(parameters.get("init_checkpoint", False)):
                command.append("--init-checkpoint")

            return command

        if service == "gdelt_discovery":
            command = [
                "gdelt-discover",
                *config_args,
                "--date",
                parameters.get("date", "today"),
            ]
            max_files = parameters.get("max_files")
            max_urls = parameters.get("max_urls")

            if max_files:
                command.extend(["--max-files", str(max_files)])

            if max_urls:
                command.extend(["--max-urls", str(max_urls)])

            return command

        if service == "url_fetch_worker":
            return [
                "article-fetch-worker",
                *config_args,
                "--max-messages",
                str(parameters.get("max_messages", 50)),
                "--request-delay-seconds",
                str(parameters.get("request_delay_seconds", 1)),
                "--output-batch-size",
                str(parameters.get("output_batch_size", 1)),
                "--output-format",
                str(parameters.get("output_format", "json")),
            ]

        raise ValueError(f"Unknown AWS collection service: {service}")

    def _workflow_arn_for_service(self, service: str) -> str:
        if service == "gdelt_discovery" and self.config.state_machine_arn:
            return self.config.state_machine_arn

        if service == "backfill" and self.config.backfill_workflow_arn:
            return self.config.backfill_workflow_arn

        if service in {"source_workflow", "ingest_standardize"} and self.config.source_workflow_arn:
            return self.config.source_workflow_arn

        raise ValueError(
            "No Step Functions workflow is configured for service "
            f"{service!r}. Use Batch or configure the service workflow ARN."
        )

    def _require_parameters(
        self,
        parameters: dict[str, Any],
        service: str,
        required: tuple[str, ...],
    ) -> None:
        missing = [key for key in required if parameters.get(key) in (None, "")]

        if missing:
            raise ValueError(
                f"{service} requires parameter(s): " + ", ".join(missing)
            )

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value

        if value is None:
            return False

        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def record_run(
        self,
        run_id: str,
        job_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not self.config.run_state_table:
            return

        self._client("dynamodb").put_item(
            TableName=self.config.run_state_table,
            Item={
                "run_id": {"S": run_id},
                "job_type": {"S": job_type},
                "status": {"S": status},
                "updated_at": {"S": utc_iso()},
                "project": {"S": self.config.project},
                "environment": {"S": self.config.environment},
                "infra_stack": {"S": self.config.base_tags["infra_stack"]},
                "payload": {"S": json.dumps(payload or {}, default=str)},
            },
        )

    def put_metric(
        self,
        name: str,
        value: int | float,
        unit: str = "Count",
        source: str = "collection",
    ) -> None:
        try:
            self._client("cloudwatch").put_metric_data(
                Namespace=self.config.cloudwatch_namespace,
                MetricData=[
                    {
                        "MetricName": name,
                        "Value": value,
                        "Unit": unit,
                        "Dimensions": [
                            {"Name": "Project", "Value": self.config.project},
                            {"Name": "Environment", "Value": self.config.environment},
                            {"Name": "Source", "Value": source},
                        ],
                    }
                ],
            )
        except ImportError:
            return
        except Exception:
            return

    def _claim_url(self, row: dict[str, Any]) -> bool:
        if not self.config.url_state_table:
            return True

        try:
            self._client("dynamodb").put_item(
                TableName=self.config.url_state_table,
                Item={
                    "url_hash": {"S": row["url_hash"]},
                    "canonical_url": {"S": row["canonical_url"]},
                    "source": {"S": row.get("source") or "unknown"},
                    "source_domain": {"S": row.get("source_domain") or ""},
                    "first_seen_at": {"S": utc_iso()},
                    "status": {"S": "queued"},
                    "run_id": {"S": row.get("run_id") or ""},
                    "project": {"S": self.config.project},
                    "environment": {"S": self.config.environment},
                    "infra_stack": {"S": self.config.base_tags["infra_stack"]},
                },
                ConditionExpression="attribute_not_exists(url_hash)",
            )
            return True
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if error.get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def _update_url_state(
        self,
        body: dict[str, Any],
        attributes: dict[str, str],
    ) -> None:
        if not self.config.url_state_table:
            return

        names = {}
        values = {}
        assignments = []

        for idx, (key, value) in enumerate(attributes.items()):
            name_key = f"#k{idx}"
            value_key = f":v{idx}"
            names[name_key] = key
            values[value_key] = {"S": str(value)}
            assignments.append(f"{name_key} = {value_key}")

        self._client("dynamodb").update_item(
            TableName=self.config.url_state_table,
            Key={"url_hash": {"S": body["url_hash"]}},
            UpdateExpression="SET " + ", ".join(assignments),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def _fetch_article_payload(self, body: dict[str, Any], run_id: str) -> dict[str, Any]:
        url = body["canonical_url"]
        response = requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent": (
                    "eml-transformer-research-bot/0.1 "
                    "(polite academic ingestion; contact project owner)"
                )
            },
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        text = "\n".join(
            p.get_text(" ", strip=True)
            for p in soup.find_all("p")
            if p.get_text(" ", strip=True)
        )
        domain = clean_domain(url) or "unknown"
        source = body.get("source") or "external_article"

        return {
            "source": "article_fetch",
            "article_source": source,
            "run_id": run_id,
            "retrieved_at": utc_iso(),
            "canonical_url": url,
            "source_url": body.get("source_url"),
            "source_domain": domain,
            "url_hash": body["url_hash"],
            "http_status": response.status_code,
            "title": title,
            "text": text,
            "html": response.text,
            "metadata": body,
            "tags": self._tags(run_id, source=source),
        }

    def _fetch_one_article(self, body: dict[str, Any], run_id: str) -> str:
        return self._write_article_payload(
            self._fetch_article_payload(body=body, run_id=run_id)
        )

    def _write_article_payload(self, payload: dict[str, Any]) -> str:
        fetch_date = datetime.now(timezone.utc).date().isoformat()
        source = payload.get("article_source") or "external_article"
        domain = payload.get("source_domain") or "unknown"
        key = (
            "bronze/articles/"
            f"source={source}/"
            f"source_domain={domain}/"
            f"fetch_date={fetch_date}/"
            f"{payload['url_hash']}.json"
        )
        self.storage.write_json(payload, key)
        return key

    def _flush_article_batch(
        self,
        batch: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
        sqs: Any,
        run_id: str,
        output_format: str,
    ) -> int:
        fetch_date = datetime.now(timezone.utc).date().isoformat()
        suffix = "jsonl.gz" if output_format.lower() == "jsonl.gz" else "jsonl"
        key = (
            "bronze/articles/batches/"
            f"fetch_date={fetch_date}/"
            f"run_id={run_id}/"
            f"{uuid.uuid4().hex}.{suffix}"
        )
        payloads = [payload for _, _, payload in batch]
        content = "".join(
            json.dumps(payload, ensure_ascii=False, default=str) + "\n"
            for payload in payloads
        ).encode("utf-8")

        if suffix == "jsonl.gz":
            self.storage.write_bytes(gzip.compress(content), key)
        else:
            self.storage.write_bytes(content, key)

        for message, body, _ in batch:
            self._update_url_state(
                body,
                {
                    "status": "fetched",
                    "article_storage_path": key,
                    "last_attempt_at": utc_iso(),
                },
            )
            sqs.delete_message(
                QueueUrl=self.config.url_fetch_queue_url,
                ReceiptHandle=message["ReceiptHandle"],
            )

        return len(batch)

    def _send_sqs_batch(
        self,
        sqs: Any,
        queue_url: str,
        entries: list[dict[str, str]],
    ) -> int:
        response = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)
        failed_ids = {item["Id"] for item in response.get("Failed", [])}
        return len([entry for entry in entries if entry["Id"] not in failed_ids])

    def _sqs_delete_batch(
        self,
        sqs: Any,
        entries: list[dict[str, str]],
    ) -> None:
        """Delete SQS messages in batches of 10 (API maximum)."""
        for batch_start in range(0, len(entries), 10):
            batch = entries[batch_start : batch_start + 10]
            sqs.delete_message_batch(
                QueueUrl=self.config.url_fetch_queue_url,
                Entries=batch,
            )

    def _try_claim_domain_throttle(
        self,
        domain: str,
        min_delay_seconds: float,
    ) -> bool:
        """Atomically claim a per-domain fetch slot using DynamoDB.

        Returns True if the request can proceed (and records the fetch
        timestamp), or False if the domain was fetched within
        ``min_delay_seconds`` by this or another worker.

        Fails open (returns True) if the throttle table is not configured or
        if an unexpected DynamoDB error occurs.
        """
        if not self.config.domain_throttle_table or min_delay_seconds <= 0:
            return True

        now = datetime.now(timezone.utc)
        earliest_allowed = (now - timedelta(seconds=min_delay_seconds)).isoformat()

        try:
            self._client("dynamodb").put_item(
                TableName=self.config.domain_throttle_table,
                Item={
                    "domain": {"S": domain},
                    "last_fetch_at": {"S": now.isoformat()},
                    "project": {"S": self.config.project},
                    "environment": {"S": self.config.environment},
                },
                ConditionExpression=(
                    "attribute_not_exists(#d) OR last_fetch_at < :earliest"
                ),
                ExpressionAttributeNames={"#d": "domain"},
                ExpressionAttributeValues={":earliest": {"S": earliest_allowed}},
            )
            return True
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if error.get("Code") == "ConditionalCheckFailedException":
                return False
            # Fail open: allow the request on unexpected errors.
            return True

    def _tags(self, run_id: str | None, source: str) -> dict[str, str]:
        tags = dict(self.config.base_tags)

        if run_id:
            tags["run_id"] = run_id

        tags["source"] = source
        return tags

    def _resolve_s3_object(
        self,
        key: str,
        bucket: str | None = None,
    ) -> tuple[str, str]:
        storage_bucket = getattr(self.storage, "bucket", None)
        resolved_bucket = bucket or storage_bucket

        if not resolved_bucket:
            raise ValueError(
                "S3 restore operations require an S3 bucket. Use S3 storage in "
                "the config or pass --bucket."
            )

        object_key = key.lstrip("/")
        object_key_mapper = getattr(self.storage, "object_key", None)

        if callable(object_key_mapper) and (bucket is None or bucket == storage_bucket):
            object_key = object_key_mapper(key)

        return resolved_bucket, object_key

    def _normalize_restore_tier(self, tier: str) -> str:
        value = tier.strip().lower().replace("_", "-")
        tiers = {
            "bulk": "Bulk",
            "standard": "Standard",
            "expedited": "Expedited",
        }

        if value not in tiers:
            raise ValueError("Restore tier must be Bulk, Standard, or Expedited.")

        return tiers[value]

    def _normalize_storage_class(self, storage_class: str) -> str:
        return storage_class.strip().upper().replace("-", "_")

    def _manifest_key(self, run_id: str, name: str) -> str:
        return f"manifests/runs/run_id={run_id}/{name}.json"
