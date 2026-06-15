from __future__ import annotations

import json
import os
from typing import Any

from aws_cdk import Aws, CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_batch as batch
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_ce as ce
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subscriptions
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from eml_transformer.deployment.config import (
    COLLECTION_SERVICES,
    build_runtime_environment,
)


class EmlTransformerCollectionStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        deployment_config: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.cfg = deployment_config
        self.infra = deployment_config["infra"]
        self.cost = deployment_config.get("cost", {})
        self.storage_cfg = deployment_config.get("storage", {})
        self.services = deployment_config.get("services", {})
        self.stack_name_prefix = self.infra["stack_name"]
        self.region_name = self.infra.get("region", "us-east-1")
        self.environment_name = self.infra.get("environment", "dev")
        self.project = self.infra.get("project", "eml_transformer")
        deployment_name = deployment_config.get("deployment", {}).get(
            "name",
            self.stack_name_prefix,
        )
        self.runtime_config_path = deployment_config.get("runtime", {}).get(
            "config_path",
            f"configs/generated/{deployment_name}.runtime.yaml",
        )

        self._tag_stack()

        notification_topic = self._create_notification_topic()
        data_lake = self._create_data_lake()
        url_fetch_dlq, url_fetch_queue = self._create_queues()
        url_state, run_state, domain_throttle = self._create_state_tables()
        repository = self._create_ecr_repository()
        log_group = self._create_log_group()
        runtime_secret_arns = self._runtime_secret_arns()
        execution_role, task_role = self._create_batch_roles(
            data_lake=data_lake,
            url_fetch_queue=url_fetch_queue,
            url_fetch_dlq=url_fetch_dlq,
            tables=[url_state, run_state, domain_throttle],
            runtime_secret_arns=runtime_secret_arns,
        )
        self._create_restore_roles(data_lake)
        compute_environment, job_queue = self._create_batch_compute()
        runtime_environment = self._runtime_environment(
            data_lake=data_lake,
            url_fetch_queue=url_fetch_queue,
            url_fetch_dlq=url_fetch_dlq,
            url_state=url_state,
            run_state=run_state,
            domain_throttle=domain_throttle,
            job_queue=job_queue,
            notification_topic=notification_topic,
        )
        job_definitions = self._create_job_definitions(
            image=(
                f"{repository.repository_uri}:"
                f"{deployment_config.get('runtime', {}).get('image_tag', 'latest')}"
            ),
            execution_role=execution_role,
            task_role=task_role,
            log_group=log_group,
            runtime_environment=runtime_environment,
        )
        gdelt_state_machine = self._create_gdelt_state_machine(
            job_queue,
            job_definitions,
            notification_topic,
        )
        source_workflow = self._create_source_workflow(
            job_queue,
            job_definitions,
            notification_topic,
        )
        backfill_workflow = self._create_backfill_workflow(
            job_queue,
            job_definitions,
            notification_topic,
        )
        self._create_scheduler(gdelt_state_machine, source_workflow)
        self._create_alarms(
            url_fetch_dlq,
            gdelt_state_machine,
            source_workflow,
            backfill_workflow,
            notification_topic,
        )
        self._create_cost_controls()
        self._create_outputs(
            data_lake=data_lake,
            repository=repository,
            url_fetch_queue=url_fetch_queue,
            url_fetch_dlq=url_fetch_dlq,
            url_state=url_state,
            run_state=run_state,
            domain_throttle=domain_throttle,
            job_queue=job_queue,
            job_definitions=job_definitions,
            gdelt_state_machine=gdelt_state_machine,
            source_workflow=source_workflow,
            backfill_workflow=backfill_workflow,
            runtime_environment=runtime_environment,
            notification_topic=notification_topic,
        )

    def _tag_stack(self) -> None:
        tags = {
            "project": self.project,
            "environment": self.environment_name,
            "infra_engine": "cdk",
            "infra_stack": self.stack_name_prefix,
            "cdk_stack": self.stack_name_prefix,
            "service_family": "collection",
        }

        for key, value in tags.items():
            Tags.of(self).add(key, str(value))

    def _create_notification_topic(self) -> sns.Topic | None:
        sns_cfg = self.cfg.get("notifications", {}).get("sns", {})

        if not sns_cfg.get("enabled", False):
            return None

        topic = sns.Topic(
            self,
            "NotificationTopic",
            topic_name=sns_cfg.get(
                "topic_name",
                f"{self.stack_name_prefix}-notifications",
            ),
        )

        for email in sns_cfg.get("email_recipients", []):
            topic.add_subscription(sns_subscriptions.EmailSubscription(email))

        return topic

    def _runtime_secret_arns(self) -> list[str]:
        secret_arns: list[str] = []

        for secret_name, secret_cfg in self.cfg.get("runtime_secrets", {}).items():
            secret_arn_env = secret_cfg.get("secret_arn_env")
            secret_arn = os.getenv(secret_arn_env or "") or secret_cfg.get("secret_arn")

            if not secret_arn:
                raise ValueError(
                    f"runtime_secrets.{secret_name} requires {secret_arn_env} "
                    "to be set to a Secrets Manager secret ARN before CDK synth/deploy."
                )

            secret_arns.append(secret_arn)

        return sorted(set(secret_arns))

    def _runtime_secrets_for_service(self, service: str) -> list[dict[str, str]]:
        secrets: list[dict[str, str]] = []

        for secret_name, secret_cfg in self.cfg.get("runtime_secrets", {}).items():
            if service not in secret_cfg.get("services", []):
                continue

            secret_arn_env = secret_cfg.get("secret_arn_env")
            secret_arn = os.getenv(secret_arn_env or "") or secret_cfg.get("secret_arn")

            if not secret_arn:
                raise ValueError(
                    f"runtime_secrets.{secret_name} requires {secret_arn_env} "
                    "to be set to a Secrets Manager secret ARN before CDK synth/deploy."
                )

            secrets.append({"name": secret_name, "valueFrom": secret_arn})

        return secrets

    def _create_data_lake(self) -> s3.Bucket:
        lifecycle = self.storage_cfg.get("lifecycle", {})
        bucket = s3.Bucket(
            self,
            "DataLakeBucket",
            bucket_name=self.storage_cfg.get(
                "bucket",
                f"{self.stack_name_prefix}-data-{Aws.ACCOUNT_ID}",
            ),
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="bronze-archive",
                    prefix="bronze/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                            transition_after=Duration.days(
                                lifecycle.get("bronze_glacier_ir_days", 90)
                            ),
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.DEEP_ARCHIVE,
                            transition_after=Duration.days(
                                lifecycle.get("bronze_deep_archive_days", 365)
                            ),
                        ),
                    ],
                ),
                s3.LifecycleRule(
                    id="restore-staging-cleanup",
                    prefix="restore-staging/",
                    expiration=Duration.days(
                        lifecycle.get("restore_staging_expiration_days", 30)
                    ),
                ),
                s3.LifecycleRule(
                    id="temporary-cleanup",
                    prefix="tmp/",
                    expiration=Duration.days(14),
                ),
                s3.LifecycleRule(
                    id="incomplete-multipart-cleanup",
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                ),
            ],
        )
        return bucket

    def _create_queues(self) -> tuple[sqs.Queue, sqs.Queue]:
        dlq = sqs.Queue(
            self,
            "UrlFetchDlq",
            queue_name=f"{self.stack_name_prefix}-url-fetch-dlq",
            retention_period=Duration.days(14),
        )
        queue = sqs.Queue(
            self,
            "UrlFetchQueue",
            queue_name=f"{self.stack_name_prefix}-url-fetch",
            visibility_timeout=Duration.seconds(300),
            retention_period=Duration.days(14),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=5,
                queue=dlq,
            ),
        )
        return dlq, queue

    def _create_state_tables(
        self,
    ) -> tuple[dynamodb.Table, dynamodb.Table, dynamodb.Table]:
        url_state = dynamodb.Table(
            self,
            "UrlStateTable",
            table_name=f"{self.stack_name_prefix}-url-state",
            partition_key=dynamodb.Attribute(
                name="url_hash",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )
        run_state = dynamodb.Table(
            self,
            "RunStateTable",
            table_name=f"{self.stack_name_prefix}-run-state",
            partition_key=dynamodb.Attribute(
                name="run_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="job_type",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )
        domain_throttle = dynamodb.Table(
            self,
            "DomainThrottleTable",
            table_name=f"{self.stack_name_prefix}-domain-throttle",
            partition_key=dynamodb.Attribute(
                name="domain",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )
        return url_state, run_state, domain_throttle

    def _create_ecr_repository(self) -> ecr.Repository:
        repository = ecr.Repository(
            self,
            "CollectionRepository",
            repository_name=f"{self.stack_name_prefix}-collection",
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        repository.add_lifecycle_rule(
            max_image_count=int(self.cost.get("ecr_retain_images", 10))
        )
        return repository

    def _create_log_group(self) -> logs.CfnLogGroup:
        return logs.CfnLogGroup(
            self,
            "BatchLogGroup",
            log_group_name=f"/aws/batch/{self.stack_name_prefix}/collection",
            retention_in_days=int(self.cost.get("log_retention_days", 30)),
        )

    def _create_batch_roles(
        self,
        data_lake: s3.Bucket,
        url_fetch_queue: sqs.Queue,
        url_fetch_dlq: sqs.Queue,
        tables: list[dynamodb.Table],
        runtime_secret_arns: list[str],
    ) -> tuple[iam.Role, iam.Role]:
        execution_role = iam.Role(
            self,
            "BatchExecutionRole",
            role_name=f"{self.stack_name_prefix}-batch-execution",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        task_role = iam.Role(
            self,
            "BatchTaskRole",
            role_name=f"{self.stack_name_prefix}-batch-task",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        data_lake.grant_read_write(task_role)
        url_fetch_queue.grant_send_messages(task_role)
        url_fetch_queue.grant_consume_messages(task_role)
        url_fetch_dlq.grant_send_messages(task_role)

        for table in tables:
            table.grant_read_write_data(task_role)

        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
            )
        )
        if runtime_secret_arns:
            execution_role.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "secretsmanager:DescribeSecret",
                        "secretsmanager:GetSecretValue",
                    ],
                    resources=runtime_secret_arns,
                )
            )
        return execution_role, task_role

    def _create_restore_roles(self, data_lake: s3.Bucket) -> None:
        restore = self.storage_cfg.get("restore", {})
        principals = restore.get("operator_principal_arns", [])

        if principals:
            role = iam.Role(
                self,
                "S3RestoreOperatorRole",
                role_name=f"{self.stack_name_prefix}-s3-restore-operator",
                assumed_by=iam.CompositePrincipal(
                    *[iam.ArnPrincipal(arn) for arn in principals]
                ),
            )
            role.add_to_policy(self._restore_policy(data_lake))

        if restore.get("bulk_restore_enabled", False):
            bulk_role = iam.Role(
                self,
                "S3BulkRestoreRole",
                role_name=f"{self.stack_name_prefix}-s3-bulk-restore",
                assumed_by=iam.ServicePrincipal("batchoperations.s3.amazonaws.com"),
            )
            bulk_role.add_to_policy(self._restore_policy(data_lake))

    def _restore_policy(self, data_lake: s3.Bucket) -> iam.PolicyStatement:
        return iam.PolicyStatement(
            actions=[
                "s3:GetBucketLocation",
                "s3:ListBucket",
                "s3:ListBucketVersions",
                "s3:GetObject",
                "s3:GetObjectVersion",
                "s3:GetObjectTagging",
                "s3:GetObjectVersionTagging",
                "s3:PutObject",
                "s3:PutObjectTagging",
                "s3:RestoreObject",
            ],
            resources=[
                data_lake.bucket_arn,
                f"{data_lake.bucket_arn}/*",
            ],
        )

    def _create_batch_compute(
        self,
    ) -> tuple[batch.CfnComputeEnvironment, batch.CfnJobQueue]:
        network = self.cfg.get("network", {})
        compute_environment = batch.CfnComputeEnvironment(
            self,
            "CollectionComputeEnvironment",
            compute_environment_name=f"{self.stack_name_prefix}-collection",
            type="MANAGED",
            compute_resources=batch.CfnComputeEnvironment.ComputeResourcesProperty(
                type="FARGATE",
                maxv_cpus=int(self.cost.get("max_batch_vcpus", 32)),
                subnets=network.get("subnet_ids", []),
                security_group_ids=network.get("security_group_ids", []),
            ),
        )
        job_queue = batch.CfnJobQueue(
            self,
            "CollectionJobQueue",
            job_queue_name=f"{self.stack_name_prefix}-collection",
            priority=1,
            state="ENABLED",
            compute_environment_order=[
                batch.CfnJobQueue.ComputeEnvironmentOrderProperty(
                    order=1,
                    compute_environment=compute_environment.ref,
                )
            ],
        )
        return compute_environment, job_queue

    def _runtime_environment(
        self,
        data_lake: s3.Bucket,
        url_fetch_queue: sqs.Queue,
        url_fetch_dlq: sqs.Queue,
        url_state: dynamodb.Table,
        run_state: dynamodb.Table,
        domain_throttle: dynamodb.Table,
        job_queue: batch.CfnJobQueue,
        notification_topic: sns.Topic | None,
    ) -> dict[str, str]:
        env = build_runtime_environment(self.cfg)
        for key in [
            "STATE_MACHINE_ARN",
            "SOURCE_WORKFLOW_ARN",
            "BACKFILL_WORKFLOW_ARN",
        ]:
            env.pop(key, None)
        for key in list(env):
            if key.startswith("BATCH_JOB_DEFINITION_"):
                env.pop(key, None)
        env.update(
            {
                "DATA_BUCKET": data_lake.bucket_name,
                "URL_FETCH_QUEUE_URL": url_fetch_queue.queue_url,
                "ARTICLE_URL_DLQ_URL": url_fetch_dlq.queue_url,
                "URL_STATE_TABLE": url_state.table_name,
                "RUN_STATE_TABLE": run_state.table_name,
                "DOMAIN_THROTTLE_TABLE": domain_throttle.table_name,
                "BATCH_JOB_QUEUE": job_queue.ref,
            }
        )
        if notification_topic:
            env["SNS_TOPIC_ARN"] = notification_topic.topic_arn
        return env

    def _create_job_definitions(
        self,
        image: str,
        execution_role: iam.Role,
        task_role: iam.Role,
        log_group: logs.CfnLogGroup,
        runtime_environment: dict[str, str],
    ) -> dict[str, batch.CfnJobDefinition]:
        job_definitions = {}

        for service in COLLECTION_SERVICES:
            service_cfg = self.services.get(service, {})
            compute = service_cfg.get("compute", {})
            timeout_seconds = int(compute.get("timeout_seconds", 3600))
            container_properties = {
                "image": image,
                "executionRoleArn": execution_role.role_arn,
                "jobRoleArn": task_role.role_arn,
                "command": self._service_command(service, service_cfg),
                "environment": [
                    {"name": key, "value": value}
                    for key, value in sorted(runtime_environment.items())
                ],
                "fargatePlatformConfiguration": {
                    "platformVersion": "LATEST"
                },
                "networkConfiguration": {
                    "assignPublicIp": (
                        "ENABLED"
                        if self.cfg.get("network", {}).get("assign_public_ip", True)
                        else "DISABLED"
                    )
                },
                "resourceRequirements": [
                    {"type": "VCPU", "value": str(compute.get("vcpu", 1))},
                    {
                        "type": "MEMORY",
                        "value": str(compute.get("memory_mib", 2048)),
                    },
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": log_group.log_group_name,
                        "awslogs-region": self.region_name,
                        "awslogs-stream-prefix": service.replace("_", "-"),
                    },
                },
            }
            runtime_secrets = self._runtime_secrets_for_service(service)

            if runtime_secrets:
                container_properties["secrets"] = runtime_secrets

            job_definitions[service] = batch.CfnJobDefinition(
                self,
                f"{_pascal(service)}JobDefinition",
                job_definition_name=(
                    f"{self.stack_name_prefix}-{service.replace('_', '-')}"
                ),
                type="container",
                platform_capabilities=["FARGATE"],
                timeout=batch.CfnJobDefinition.TimeoutProperty(
                    attempt_duration_seconds=timeout_seconds,
                ),
                retry_strategy=batch.CfnJobDefinition.RetryStrategyProperty(
                    attempts=int(service_cfg.get("retry_attempts", 1)),
                ),
                container_properties=container_properties,
            )

        return job_definitions

    def _service_command(self, service: str, service_cfg: dict[str, Any]) -> list[str]:
        # No --config arg: runtime values are injected as environment variables
        # by CDK via _runtime_environment() -> build_runtime_environment().
        # The CLI build_aws_runtime() falls back to env-vars-only mode when the
        # config file is absent in the container.

        # ingest and standardize use Ref::source so the source can be supplied
        # both by Step Functions parameter substitution and by direct Batch
        # containerOverrides (which overrides the command entirely).
        if service in {"ingest", "standardize"}:
            return [
                "service-run",
                "--service",
                service,
                "--source",
                "Ref::source",
            ]

        if service in {"embed", "run_all"}:
            command = [
                "service-run",
                "--service",
                service,
                "--source",
                service_cfg.get("source", "all"),
            ]
            if service == "embed" and service_cfg.get("model_name"):
                command.extend(["--model", service_cfg["model_name"]])
            return command

        if service == "backfill":
            command = [
                "service-run",
                "--service",
                "backfill",
                "--source",
                "Ref::source",
                "--start-date",
                "Ref::start_date",
                "--end-date",
                "Ref::end_date",
                "--window-days",
                "Ref::window_days",
            ]
            if service_cfg.get("init_checkpoint", False):
                command.append("--init-checkpoint")
            return command

        if service == "gdelt_discovery":
            command = [
                "gdelt-discover",
                "--date",
                "Ref::date",
            ]
            acquisition = self.cfg.get("sources", {}).get("gdelt", {}).get(
                "acquisition",
                {},
            )
            if acquisition.get("max_files"):
                command.extend(["--max-files", str(acquisition["max_files"])])
            if acquisition.get("max_urls_per_run"):
                command.extend(["--max-urls", str(acquisition["max_urls_per_run"])])
            return command

        if service == "url_fetch_worker":
            limits = service_cfg.get("limits", {})
            return [
                "article-fetch-worker",
                "--max-messages",
                str(limits.get("max_messages", 50)),
                "--request-delay-seconds",
                str(limits.get("request_delay_seconds", 1)),
                "--output-batch-size",
                str(limits.get("output_batch_size", 1)),
                "--output-format",
                str(limits.get("output_format", "json")),
            ]

        if service == "s3_restore_operator":
            restore_cfg = service_cfg.get("restore", {})
            return [
                "aws-restore-s3-object",
                "--key",
                "Ref::key",
                "--days",
                str(restore_cfg.get("days", 7)),
                "--tier",
                restore_cfg.get("tier", "Bulk"),
            ]

        raise ValueError(f"Unsupported service: {service}")

    def _sfn_role(
        self,
        logical_id: str,
        role_name: str,
        job_queue: batch.CfnJobQueue,
        job_definitions: dict[str, batch.CfnJobDefinition],
        notification_topic: sns.Topic | None = None,
    ) -> iam.Role:
        """Create a Step Functions execution role with Batch permissions."""
        batch_submit_resources = [
            job_queue.attr_job_queue_arn,
            *[jd.attr_job_definition_arn for jd in job_definitions.values()],
        ]
        step_functions_batch_rule = (
            f"arn:{Aws.PARTITION}:events:{Aws.REGION}:"
            f"{Aws.ACCOUNT_ID}:rule/StepFunctionsGetEventsForBatchJobsRule"
        )
        statements = [
            iam.PolicyStatement(
                actions=["batch:SubmitJob"],
                resources=batch_submit_resources,
            ),
            iam.PolicyStatement(
                actions=["batch:DescribeJobs", "batch:TerminateJob"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                actions=[
                    "events:PutTargets",
                    "events:PutRule",
                    "events:DescribeRule",
                ],
                resources=[step_functions_batch_rule],
            ),
        ]

        if notification_topic:
            statements.append(
                iam.PolicyStatement(
                    actions=["sns:Publish"],
                    resources=[notification_topic.topic_arn],
                )
            )

        return iam.Role(
            self,
            logical_id,
            role_name=role_name,
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
            inline_policies={
                "StepFunctionsBatchIntegration": iam.PolicyDocument(
                    statements=statements
                )
            },
        )

    def _notify_state(
        self,
        notification_topic: sns.Topic,
        workflow: str,
        status: str,
        *,
        next_state: str | None = None,
        end: bool = False,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "Type": "Task",
            "Resource": "arn:aws:states:::sns:publish",
            "Parameters": {
                "TopicArn": notification_topic.topic_arn,
                "Subject": f"{self.stack_name_prefix} {workflow} {status}",
                "Message.$": (
                    "States.Format("
                    "'stack={} workflow={} status={} execution={} input={}', "
                    f"'{self.stack_name_prefix}', "
                    f"'{workflow}', "
                    f"'{status}', "
                    "$$.Execution.Id, "
                    "States.JsonToString($$.Execution.Input)"
                    ")"
                ),
            },
            "ResultPath": "$.notification",
        }

        if end:
            state["End"] = True
        elif next_state:
            state["Next"] = next_state

        return state

    def _failure_catch(self) -> list[dict[str, str]]:
        return [
            {
                "ErrorEquals": ["States.ALL"],
                "ResultPath": "$.error",
                "Next": "NotifyFailure",
            }
        ]

    def _notification_terminal_states(
        self,
        notification_topic: sns.Topic,
        workflow: str,
        success_subject: str = "completed",
    ) -> dict[str, Any]:
        return {
            "NotifySuccess": self._notify_state(
                notification_topic,
                workflow,
                success_subject,
                end=True,
            ),
            "NotifyFailure": self._notify_state(
                notification_topic,
                workflow,
                "failed",
                next_state="WorkflowFailed",
            ),
            "WorkflowFailed": {
                "Type": "Fail",
                "Error": f"{workflow}Failed",
                "Cause": f"{workflow} workflow failed; notification was published.",
            },
        }

    def _create_gdelt_state_machine(
        self,
        job_queue: batch.CfnJobQueue,
        job_definitions: dict[str, batch.CfnJobDefinition],
        notification_topic: sns.Topic | None,
    ) -> sfn.CfnStateMachine:
        """High-volume GDELT acquisition workflow: discover → parallel URL fetch workers."""
        role = self._sfn_role(
            "GdeltStepFunctionsRole",
            f"{self.stack_name_prefix}-gdelt-step-functions",
            job_queue,
            job_definitions,
            notification_topic,
        )
        url_fetch_cfg = self.services.get("url_fetch_worker", {})
        parallelism = int(
            url_fetch_cfg.get("limits", {}).get("worker_parallelism", 1)
        )

        fetch_task = {
            "Type": "Task",
            "Resource": "arn:aws:states:::batch:submitJob.sync",
            "Parameters": {
                "JobName.$": "States.Format('url-fetch-worker-{}', States.UUID())",
                "JobQueue": job_queue.ref,
                "JobDefinition": job_definitions["url_fetch_worker"].ref,
            },
            "ResultPath": "$.fetch_result",
            "Retry": [
                {
                    "ErrorEquals": ["States.TaskFailed"],
                    "IntervalSeconds": 60,
                    "MaxAttempts": 2,
                    "BackoffRate": 2,
                }
            ],
        }

        if parallelism <= 1:
            fetch_state: dict[str, Any] = dict(fetch_task)
            if notification_topic:
                fetch_state["Next"] = "NotifySuccess"
                fetch_state["Catch"] = self._failure_catch()
            else:
                fetch_state["End"] = True
            fetch_state_name = "FetchUrls"
            fetch_states = {fetch_state_name: fetch_state}
        else:
            # Fan-out: run N parallel fetch workers after discovery.
            branches = [
                {
                    "StartAt": f"FetchWorker{i + 1}",
                    "States": {
                        f"FetchWorker{i + 1}": {
                            **fetch_task,
                            "Parameters": {
                                **fetch_task["Parameters"],
                                "JobName.$": (
                                    f"States.Format('url-fetch-worker-{i + 1}-{{}}', States.UUID())"
                                ),
                            },
                            "End": True,
                        }
                    },
                }
                for i in range(parallelism)
            ]
            fetch_state_name = "FetchUrlsParallel"
            parallel_state: dict[str, Any] = {
                "Type": "Parallel",
                "Branches": branches,
                "ResultPath": "$.fetch_results",
            }
            if notification_topic:
                parallel_state["Next"] = "NotifySuccess"
                parallel_state["Catch"] = self._failure_catch()
            else:
                parallel_state["End"] = True
            fetch_states = {fetch_state_name: parallel_state}

        definition = {
            "Comment": "High-volume GDELT URL acquisition workflow",
            "StartAt": "DiscoverGdeltUrls",
            "States": {
                "DiscoverGdeltUrls": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::batch:submitJob.sync",
                    "Parameters": {
                        "JobName.$": "States.Format('gdelt-discovery-{}', States.UUID())",
                        "JobQueue": job_queue.ref,
                        "JobDefinition": job_definitions["gdelt_discovery"].ref,
                        "Parameters": {
                            "date.$": "$.parameters.date",
                        },
                    },
                    "Retry": [
                        {
                            "ErrorEquals": ["States.TaskFailed"],
                            "IntervalSeconds": 60,
                            "MaxAttempts": 2,
                            "BackoffRate": 2,
                        }
                    ],
                    "ResultPath": "$.discovery_result",
                    "Next": fetch_state_name,
                },
                **fetch_states,
            },
        }
        if notification_topic:
            definition["States"]["DiscoverGdeltUrls"]["Catch"] = self._failure_catch()
            definition["States"].update(
                self._notification_terminal_states(
                    notification_topic,
                    "GDELT acquisition",
                )
            )
        return sfn.CfnStateMachine(
            self,
            "GdeltAcquisitionStateMachine",
            state_machine_name=f"{self.stack_name_prefix}-acquisition",
            role_arn=role.role_arn,
            definition_string=json.dumps(definition),
        )

    def _create_source_workflow(
        self,
        job_queue: batch.CfnJobQueue,
        job_definitions: dict[str, batch.CfnJobDefinition],
        notification_topic: sns.Topic | None,
    ) -> sfn.CfnStateMachine:
        """Generic source ingest → standardize workflow for non-GDELT sources."""
        role = self._sfn_role(
            "SourceWorkflowRole",
            f"{self.stack_name_prefix}-source-workflow",
            job_queue,
            job_definitions,
            notification_topic,
        )
        retry = [
            {
                "ErrorEquals": ["States.TaskFailed"],
                "IntervalSeconds": 60,
                "MaxAttempts": 2,
                "BackoffRate": 2,
            }
        ]
        definition = {
            "Comment": "Generic source ingest and standardize workflow",
            "StartAt": "IngestSource",
            "States": {
                "IngestSource": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::batch:submitJob.sync",
                    "Parameters": {
                        "JobName.$": (
                            "States.Format('ingest-{}-{}', $.parameters.source, States.UUID())"
                        ),
                        "JobQueue": job_queue.ref,
                        "JobDefinition": job_definitions["ingest"].ref,
                        "Parameters": {
                            "source.$": "$.parameters.source",
                        },
                    },
                    "Retry": retry,
                    "ResultPath": "$.ingest_result",
                    "Next": "StandardizeSource",
                },
                "StandardizeSource": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::batch:submitJob.sync",
                    "Parameters": {
                        "JobName.$": (
                            "States.Format('standardize-{}-{}', $.parameters.source, States.UUID())"
                        ),
                        "JobQueue": job_queue.ref,
                        "JobDefinition": job_definitions["standardize"].ref,
                        "Parameters": {
                            "source.$": "$.parameters.source",
                        },
                    },
                    "Retry": retry,
                    "ResultPath": "$.standardize_result",
                },
            },
        }
        if notification_topic:
            definition["States"]["IngestSource"]["Catch"] = self._failure_catch()
            definition["States"]["StandardizeSource"]["Catch"] = self._failure_catch()
            definition["States"]["StandardizeSource"]["Next"] = "NotifySuccess"
            definition["States"].update(
                self._notification_terminal_states(
                    notification_topic,
                    "source workflow",
                )
            )
        else:
            definition["States"]["StandardizeSource"]["End"] = True
        return sfn.CfnStateMachine(
            self,
            "SourceWorkflowStateMachine",
            state_machine_name=f"{self.stack_name_prefix}-source-workflow",
            role_arn=role.role_arn,
            definition_string=json.dumps(definition),
        )

    def _create_backfill_workflow(
        self,
        job_queue: batch.CfnJobQueue,
        job_definitions: dict[str, batch.CfnJobDefinition],
        notification_topic: sns.Topic | None,
    ) -> sfn.CfnStateMachine:
        """Parameterized backfill workflow: source + start_date + end_date + window_days."""
        role = self._sfn_role(
            "BackfillWorkflowRole",
            f"{self.stack_name_prefix}-backfill-workflow",
            job_queue,
            job_definitions,
            notification_topic,
        )
        base_command = (
            "States.Array('service-run', '--service', 'backfill', '--source', "
            "$.parameters.source, '--start-date', $.parameters.start_date, "
            "'--end-date', $.parameters.end_date, '--window-days', "
            "States.Format('{}', $.parameters.window_days))"
        )
        checkpoint_command = (
            "States.Array('service-run', '--service', 'backfill', '--source', "
            "$.parameters.source, '--start-date', $.parameters.start_date, "
            "'--end-date', $.parameters.end_date, '--window-days', "
            "States.Format('{}', $.parameters.window_days), '--init-checkpoint')"
        )
        base_task = {
            "Type": "Task",
            "Resource": "arn:aws:states:::batch:submitJob.sync",
            "Parameters": {
                "JobName.$": (
                    "States.Format('backfill-{}-{}', $.parameters.source, States.UUID())"
                ),
                "JobQueue": job_queue.ref,
                "JobDefinition": job_definitions["backfill"].ref,
                "ContainerOverrides": {
                    "Command.$": base_command,
                },
            },
            "Retry": [
                {
                    "ErrorEquals": ["States.TaskFailed"],
                    "IntervalSeconds": 120,
                    "MaxAttempts": 1,
                    "BackoffRate": 1,
                }
            ],
            "ResultPath": "$.backfill_result",
        }
        checkpoint_task = {
            **base_task,
            "Parameters": {
                **base_task["Parameters"],
                "ContainerOverrides": {
                    "Command.$": checkpoint_command,
                },
            },
        }
        definition = {
            "Comment": "Parameterized backfill workflow",
            "StartAt": "CheckSeedCheckpoint",
            "States": {
                "CheckSeedCheckpoint": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.parameters.init_checkpoint",
                            "BooleanEquals": True,
                            "Next": "RunBackfillAndSeedCheckpoint",
                        }
                    ],
                    "Default": "RunBackfill",
                },
                "RunBackfill": base_task,
                "RunBackfillAndSeedCheckpoint": checkpoint_task,
            },
        }
        if notification_topic:
            definition["States"]["RunBackfill"]["Catch"] = self._failure_catch()
            definition["States"]["RunBackfill"]["Next"] = "NotifySuccess"
            definition["States"]["RunBackfillAndSeedCheckpoint"]["Catch"] = (
                self._failure_catch()
            )
            definition["States"]["RunBackfillAndSeedCheckpoint"]["Next"] = (
                "NotifySuccess"
            )
            definition["States"].update(
                self._notification_terminal_states(
                    notification_topic,
                    "backfill",
                    "completed",
                )
            )
        else:
            definition["States"]["RunBackfill"]["End"] = True
            definition["States"]["RunBackfillAndSeedCheckpoint"]["End"] = True
        return sfn.CfnStateMachine(
            self,
            "BackfillWorkflowStateMachine",
            state_machine_name=f"{self.stack_name_prefix}-backfill-workflow",
            role_arn=role.role_arn,
            definition_string=json.dumps(definition),
        )

    def _create_scheduler(
        self,
        gdelt_state_machine: sfn.CfnStateMachine,
        source_workflow: sfn.CfnStateMachine,
    ) -> None:
        """Create EventBridge schedules for services that have scheduling enabled."""
        # GDELT acquisition schedule
        gdelt_service = self.services.get("gdelt_discovery", {})
        gdelt_schedule = gdelt_service.get("schedule", {})

        if gdelt_schedule.get("enabled", False):
            gdelt_role = iam.Role(
                self,
                "GdeltSchedulerRole",
                role_name=f"{self.stack_name_prefix}-gdelt-scheduler",
                assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
            )
            gdelt_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["states:StartExecution"],
                    resources=[gdelt_state_machine.attr_arn],
                )
            )
            scheduler.CfnSchedule(
                self,
                "GdeltAcquisitionSchedule",
                name=f"{self.stack_name_prefix}-gdelt-acquisition",
                schedule_expression=gdelt_schedule.get("expression", "rate(1 hour)"),
                state="ENABLED",
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                    mode="OFF",
                ),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=gdelt_state_machine.attr_arn,
                    role_arn=gdelt_role.role_arn,
                    input=json.dumps(
                        {
                            "service": "gdelt_discovery",
                            "parameters": {"date": "today"},
                        }
                    ),
                ),
            )

        # Generic source workflow schedule: one scheduled execution runs
        # ingest -> standardize for the configured source selector.
        source_service = self.services.get("ingest", {})
        source_schedule = source_service.get("schedule", {})

        if source_schedule.get("enabled", False):
            source_schedule_role = iam.Role(
                self,
                "SourceSchedulerRole",
                role_name=f"{self.stack_name_prefix}-source-scheduler",
                assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
            )
            source_schedule_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["states:StartExecution"],
                    resources=[source_workflow.attr_arn],
                )
            )
            source = source_service.get("source", "all")
            scheduler.CfnSchedule(
                self,
                "SourceWorkflowSchedule",
                name=f"{self.stack_name_prefix}-source-workflow-{source}",
                schedule_expression=source_schedule.get("expression", "rate(1 day)"),
                state="ENABLED",
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                    mode="OFF",
                ),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=source_workflow.attr_arn,
                    role_arn=source_schedule_role.role_arn,
                    input=json.dumps(
                        {
                            "service": "source_workflow",
                            "parameters": {"source": source},
                        }
                    ),
                ),
            )

    def _create_alarms(
        self,
        url_fetch_dlq: sqs.Queue,
        gdelt_state_machine: sfn.CfnStateMachine,
        source_workflow: sfn.CfnStateMachine,
        backfill_workflow: sfn.CfnStateMachine,
        notification_topic: sns.Topic | None,
    ) -> None:
        # SQS DLQ depth alarm
        dlq_alarm = cloudwatch.Alarm(
            self,
            "UrlFetchDlqVisibleAlarm",
            alarm_name=f"{self.stack_name_prefix}-url-fetch-dlq-visible",
            metric=url_fetch_dlq.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="URL fetch messages reached the DLQ.",
        )
        self._add_alarm_action(dlq_alarm, notification_topic)

        # Batch job failure alarm
        batch_alarm = cloudwatch.Alarm(
            self,
            "BatchJobFailureAlarm",
            alarm_name=f"{self.stack_name_prefix}-batch-job-failures",
            metric=cloudwatch.Metric(
                namespace="AWS/Batch",
                metric_name="FailedJobCount",
                dimensions_map={
                    "JobQueue": f"{self.stack_name_prefix}-collection",
                },
                period=Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="One or more Batch jobs failed in the collection queue.",
        )
        self._add_alarm_action(batch_alarm, notification_topic)

        # Step Functions execution failure alarms
        for sfn_machine, alarm_id, alarm_suffix in (
            (gdelt_state_machine, "GdeltSfnFailureAlarm", "gdelt-sfn-failures"),
            (source_workflow, "SourceWorkflowFailureAlarm", "source-workflow-failures"),
            (backfill_workflow, "BackfillWorkflowFailureAlarm", "backfill-workflow-failures"),
        ):
            alarm = cloudwatch.Alarm(
                self,
                alarm_id,
                alarm_name=f"{self.stack_name_prefix}-{alarm_suffix}",
                metric=cloudwatch.Metric(
                    namespace="AWS/States",
                    metric_name="ExecutionsFailed",
                    dimensions_map={
                        "StateMachineArn": sfn_machine.attr_arn,
                    },
                    period=Duration.minutes(5),
                    statistic="Sum",
                ),
                threshold=0,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                alarm_description=f"Step Functions executions failed for {alarm_suffix}.",
            )
            self._add_alarm_action(alarm, notification_topic)

    def _add_alarm_action(
        self,
        alarm: cloudwatch.Alarm,
        notification_topic: sns.Topic | None,
    ) -> None:
        if notification_topic:
            alarm.add_alarm_action(cloudwatch_actions.SnsAction(notification_topic))

    def _create_cost_controls(self) -> None:
        emails = self.cost.get("alert_emails", [])
        budget_limit = float(self.cost.get("monthly_budget_usd", 0))

        if budget_limit > 0 and emails:
            budgets.CfnBudget(
                self,
                "MonthlyBudget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=f"{self.stack_name_prefix}-monthly",
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(
                        amount=budget_limit,
                        unit="USD",
                    ),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            comparison_operator="GREATER_THAN",
                            notification_type="ACTUAL",
                            threshold=80,
                            threshold_type="PERCENTAGE",
                        ),
                        subscribers=[
                            budgets.CfnBudget.SubscriberProperty(
                                address=email,
                                subscription_type="EMAIL",
                            )
                            for email in emails
                        ],
                    )
                ],
            )

        if self.cost.get("cost_anomaly_detection", True) and emails:
            monitor = ce.CfnAnomalyMonitor(
                self,
                "CostAnomalyMonitor",
                monitor_name=f"{self.stack_name_prefix}-service-costs",
                monitor_type="DIMENSIONAL",
                monitor_dimension="SERVICE",
            )
            ce.CfnAnomalySubscription(
                self,
                "CostAnomalySubscription",
                subscription_name=f"{self.stack_name_prefix}-cost-anomalies",
                frequency="DAILY",
                monitor_arn_list=[monitor.attr_monitor_arn],
                subscribers=[
                    ce.CfnAnomalySubscription.SubscriberProperty(
                        address=email,
                        type="EMAIL",
                    )
                    for email in emails
                ],
                threshold_expression={
                    "Dimensions": {
                        "Key": "ANOMALY_TOTAL_IMPACT_ABSOLUTE",
                        "Values": [
                            str(self.cost.get("cost_anomaly_threshold_usd", 10))
                        ],
                        "MatchOptions": ["GREATER_THAN_OR_EQUAL"],
                    }
                },
            )

    def _create_outputs(
        self,
        data_lake: s3.Bucket,
        repository: ecr.Repository,
        url_fetch_queue: sqs.Queue,
        url_fetch_dlq: sqs.Queue,
        url_state: dynamodb.Table,
        run_state: dynamodb.Table,
        domain_throttle: dynamodb.Table,
        job_queue: batch.CfnJobQueue,
        job_definitions: dict[str, batch.CfnJobDefinition],
        gdelt_state_machine: sfn.CfnStateMachine,
        source_workflow: sfn.CfnStateMachine,
        backfill_workflow: sfn.CfnStateMachine,
        runtime_environment: dict[str, str],
        notification_topic: sns.Topic | None,
    ) -> None:
        outputs = {
            "DataBucketName": data_lake.bucket_name,
            "UrlFetchQueueUrl": url_fetch_queue.queue_url,
            "UrlFetchDlqUrl": url_fetch_dlq.queue_url,
            "UrlStateTable": url_state.table_name,
            "RunStateTable": run_state.table_name,
            "DomainThrottleTable": domain_throttle.table_name,
            "EcrRepositoryUrl": repository.repository_uri,
            "BatchJobQueue": job_queue.ref,
            # Primary acquisition state machine (GDELT high-volume path).
            "StateMachineArn": gdelt_state_machine.attr_arn,
            # Generic source and backfill workflow ARNs.
            "SourceWorkflowArn": source_workflow.attr_arn,
            "BackfillWorkflowArn": backfill_workflow.attr_arn,
            "RuntimeEnvironment": self.to_json_string(runtime_environment),
            "ArchivePolicy": json.dumps(
                {
                    "bronze_prefix": "bronze/",
                    "standard_days": (
                        "0-"
                        f"{self.storage_cfg.get('lifecycle', {}).get('bronze_glacier_ir_days', 90)}"
                    ),
                    "glacier_instant_retrieval_days": (
                        f"{self.storage_cfg.get('lifecycle', {}).get('bronze_glacier_ir_days', 90)}-"
                        f"{self.storage_cfg.get('lifecycle', {}).get('bronze_deep_archive_days', 365)}"
                    ),
                    "deep_archive_after_days": self.storage_cfg.get(
                        "lifecycle",
                        {},
                    ).get("bronze_deep_archive_days", 365),
                }
            ),
        }
        if notification_topic:
            outputs["SnsTopicArn"] = notification_topic.topic_arn

        for service, job_definition in job_definitions.items():
            outputs[f"{_pascal(service)}JobDefinition"] = job_definition.ref

        for name, value in outputs.items():
            output = CfnOutput(self, f"{name}Output", value=value)
            output.override_logical_id(name)


def _pascal(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))
