"""Compatibility exports for deployment configuration helpers."""

from eml_transformer.deployment.cfn_outputs import (
    _pascal_to_snake,
    render_runtime_config_from_cfn_outputs,
)
from eml_transformer.deployment.loader import (
    deep_merge,
    load_deployment_config,
    load_yaml,
    write_yaml,
)
from eml_transformer.deployment.metadata import (
    cfn_outputs_path,
    deployment_matrix,
    deployment_metadata,
    deployment_name,
    runtime_config_path,
)
from eml_transformer.deployment.model import COLLECTION_SERVICES, DeploymentConfig
from eml_transformer.deployment.rendering import (
    build_runtime_environment,
    render_runtime_config,
    service_job_definition_arns,
)
from eml_transformer.deployment.validation import (
    assert_valid_deployment_config,
    deployment_config_warnings,
    validate_deployment_config,
)


__all__ = [
    "COLLECTION_SERVICES",
    "DeploymentConfig",
    "assert_valid_deployment_config",
    "build_runtime_environment",
    "cfn_outputs_path",
    "deep_merge",
    "deployment_config_warnings",
    "deployment_matrix",
    "deployment_metadata",
    "deployment_name",
    "load_deployment_config",
    "load_yaml",
    "render_runtime_config",
    "render_runtime_config_from_cfn_outputs",
    "runtime_config_path",
    "service_job_definition_arns",
    "validate_deployment_config",
    "write_yaml",
    "_pascal_to_snake",
]
