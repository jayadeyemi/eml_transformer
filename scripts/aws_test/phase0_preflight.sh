#!/usr/bin/env bash
# phase0_preflight.sh -- AWS SSO login, config render, CDK deploy, ECR push
# Usage: bash scripts/aws_test/phase0_preflight.sh
# Host operations only (CDK, SSO, ECR push); everything else is in-container.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

DEPLOYMENT_CONFIG="${REPO_ROOT}/configs/deployments/aws-smoke.yaml"
STACK_NAME="eml-transformer-smoke"
REGION="${AWS_REGION:-us-east-1}"

log "=== Phase 0: Pre-flight Setup ==="

# 0c: Verify .gitignore covers sensitive paths
if ! grep -q "plan/" "${REPO_ROOT}/.gitignore"; then
    warn ".gitignore missing plan/ -- check your .gitignore"
fi

# 0d-1: SSO login (host -- required for CDK deploy and ECR push)
ACCOUNT_ID="$(check_sso_token)"
export ACCOUNT_ID

# 0d-2: Config render (draft for CDK synth) -- runs in container
log "Rendering draft runtime config (placeholder ARNs for CDK synth)"
mkdir -p "${REPO_ROOT}/configs/generated"
docker run --rm \
    -v "${REPO_ROOT}/configs:/app/configs" \
    "${IMAGE_TAG}" \
    config-render \
    --deployment configs/deployments/aws-smoke.yaml \
    --output configs/generated/aws-smoke.runtime.yaml 2>&1 \
    || {
        # First run: image may not exist yet; fall back to in-place render
        warn "Container not yet built; using installed eml_transformer for draft render"
        eml_transformer config-render \
            --deployment "${DEPLOYMENT_CONFIG}" \
            --output "${RUNTIME_CONFIG}" 2>&1
    }

# 0d-3: Docker build (single build -- no RUNTIME_CONFIG baked in)
log "Building Docker image: ${IMAGE_TAG} (extras: ${BUILD_EXTRAS})"
docker build \
    --build-arg "OPTIONAL_EXTRAS=${BUILD_EXTRAS}" \
    -t "${IMAGE_TAG}" \
    "${REPO_ROOT}"

# 0d-4: CDK bootstrap (idempotent, host)
log "CDK bootstrap (account=${ACCOUNT_ID}, region=${REGION})"
cd "${REPO_ROOT}/infra/cdk"
pip install -q -r requirements.txt
cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}" --profile "${AWS_PROFILE}"

# 0d-5: CDK deploy smoke stack (host)
log "CDK deploy: ${STACK_NAME}"
cdk deploy "${STACK_NAME}" \
    --profile "${AWS_PROFILE}" \
    --require-approval never \
    --outputs-file "${REPO_ROOT}/configs/generated/aws-smoke.cfn-outputs.json" \
    --context deployment=smoke
cd "${REPO_ROOT}"

# 0d-6: Render real runtime config from CFN outputs -- runs in container
log "Rendering real runtime config from CloudFormation outputs"
docker_run_aws "${RESULTS_DIR}/phase0_config_render_from_outputs.log" -- \
    config-render-from-outputs \
    --stack "${STACK_NAME}" \
    --region "${REGION}" \
    --output configs/generated/aws-smoke.runtime.yaml

# Copy the rendered config out of the container back to the host
# (The container wrote it to its own /app/configs/generated/ via the volume mount above)
# Actually config-render-from-outputs writes to the path inside the container.
# We need to use a bind mount so the file is written to the host.
log "Re-rendering runtime config with host volume mount"
docker run --rm \
    -v "${HOME}/.aws:/root/.aws" \
    -e AWS_PROFILE="${AWS_PROFILE}" \
    -e AWS_SDK_LOAD_CONFIG=1 \
    -e AWS_REGION="${REGION}" \
    -v "${REPO_ROOT}/configs/generated:/app/configs/generated" \
    "${IMAGE_TAG}" \
    config-render-from-outputs \
    --stack "${STACK_NAME}" \
    --region "${REGION}" \
    --output configs/generated/aws-smoke.runtime.yaml

# 0d-7: ECR push (host -- requires docker login)
log "Pushing Docker image to ECR"
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${STACK_NAME}-collection"
aws ecr get-login-password --region "${REGION}" --profile "${AWS_PROFILE}" \
    | docker login --username AWS --password-stdin \
        "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

docker tag "${IMAGE_TAG}" "${ECR_REPO}:smoke"
docker tag "${IMAGE_TAG}" "${ECR_REPO}:latest"
docker push "${ECR_REPO}:smoke"
docker push "${ECR_REPO}:latest"

log "Phase 0 complete."
log "  Runtime config: ${RUNTIME_CONFIG}"
log "  ECR image:      ${ECR_REPO}:smoke"
