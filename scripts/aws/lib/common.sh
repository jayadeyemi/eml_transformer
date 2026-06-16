#!/usr/bin/env bash
# Minimal bootstrap for AWS operator wrappers.
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-episb}"
AWS_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${AWS_SCRIPT_ROOT}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python || true)}"

[[ -n "${PYTHON_BIN}" ]] || {
    echo "python3 or python is required" >&2
    exit 1
}

export AWS_PROFILE
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

require_deployment_config() {
    [[ -n "${DEPLOYMENT_CONFIG:-}" ]] || {
        echo "DEPLOYMENT_CONFIG is required. Example: DEPLOYMENT_CONFIG=configs/deployments/aws-dev.yaml" >&2
        exit 1
    }
}

run_eml_transformer() {
    "${PYTHON_BIN}" -m eml_transformer.cli "$@"
}
