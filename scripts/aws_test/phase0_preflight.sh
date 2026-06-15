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
SCHEDULE_TEST_EXPRESSION="${SCHEDULE_TEST_EXPRESSION:-rate(10 minutes)}"

log "=== Phase 0: Pre-flight Setup ==="

# 0c: Verify .gitignore covers sensitive paths
if ! grep -q "plan/" "${REPO_ROOT}/.gitignore"; then
    warn ".gitignore missing plan/ -- check your .gitignore"
fi

# 0d-1: SSO login (host -- required for CDK deploy and ECR push)
ACCOUNT_ID="$(check_sso_token)"
export ACCOUNT_ID

if [[ -z "${NEWSAPI_SECRET_NAME:-}" ]]; then
    fail "NEWSAPI_SECRET_NAME is required. Create the NewsAPI key in Secrets Manager and pass its secret name."
fi

log "Resolving NEWSAPI secret ARN from Secrets Manager (name only; no secret value is read)"
NEWSAPI_SECRET_ARN="$(aws secretsmanager describe-secret \
    --secret-id "${NEWSAPI_SECRET_NAME}" \
    --region "${REGION}" \
    --profile "${AWS_PROFILE}" \
    --query ARN \
    --output text)"
export NEWSAPI_SECRET_ARN
info "Resolved NEWSAPI secret ARN."

log "Resolving default VPC network for AWS Batch Fargate"
DEFAULT_VPC_ID="$(aws ec2 describe-vpcs \
    --filters Name=is-default,Values=true \
    --region "${REGION}" \
    --profile "${AWS_PROFILE}" \
    --query 'Vpcs[0].VpcId' \
    --output text)"
if [[ -z "${DEFAULT_VPC_ID}" || "${DEFAULT_VPC_ID}" == "None" ]]; then
    fail "No default VPC found in ${REGION}; set BATCH_SUBNET_IDS and BATCH_SECURITY_GROUP_IDS before running preflight."
fi

if [[ -z "${BATCH_SUBNET_IDS:-}" ]]; then
    BATCH_SUBNET_IDS="$(aws ec2 describe-subnets \
        --filters Name=vpc-id,Values="${DEFAULT_VPC_ID}" \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        --query 'Subnets[].SubnetId' \
        --output text | tr '\t' ',')"
fi
if [[ -z "${BATCH_SECURITY_GROUP_IDS:-}" ]]; then
    BATCH_SECURITY_GROUP_IDS="$(aws ec2 describe-security-groups \
        --filters Name=vpc-id,Values="${DEFAULT_VPC_ID}" Name=group-name,Values=default \
        --region "${REGION}" \
        --profile "${AWS_PROFILE}" \
        --query 'SecurityGroups[0].GroupId' \
        --output text)"
fi
export BATCH_SUBNET_IDS
export BATCH_SECURITY_GROUP_IDS
info "Using VPC ${DEFAULT_VPC_ID} with subnets ${BATCH_SUBNET_IDS} and security groups ${BATCH_SECURITY_GROUP_IDS}."

# 0d-2: Docker build (single build -- no RUNTIME_CONFIG baked in)
mkdir -p "${REPO_ROOT}/configs/generated"
log "Building Docker image: ${IMAGE_TAG} (extras: ${BUILD_EXTRAS})"
docker build \
    --build-arg "OPTIONAL_EXTRAS=${BUILD_EXTRAS}" \
    -t "${IMAGE_TAG}" \
    "${REPO_ROOT}"

# 0d-3: CDK bootstrap (idempotent, host)
log "CDK bootstrap (account=${ACCOUNT_ID}, region=${REGION})"
cd "${REPO_ROOT}/infra/cdk"
CDK_PYTHON="${CDK_PYTHON:-${REPO_ROOT}/.venv-cdk/bin/python}"
if [[ ! -x "${CDK_PYTHON}" ]]; then
    python3 -m venv "${REPO_ROOT}/.venv-cdk"
fi
"${CDK_PYTHON}" -m pip install -q -r requirements.txt
CDK_APP="\"${CDK_PYTHON}\" app.py"
cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}" \
    --app "${CDK_APP}" \
    --profile "${AWS_PROFILE}"

# 0d-4: CDK deploy smoke stack (host)
log "CDK deploy: ${STACK_NAME}"
cdk deploy "${STACK_NAME}" \
    --app "${CDK_APP}" \
    --profile "${AWS_PROFILE}" \
    --require-approval never \
    --outputs-file "${REPO_ROOT}/configs/generated/aws-smoke.cfn-outputs.json" \
    -c "deployment_config=configs/deployments/aws-smoke.yaml" \
    -c "image_tag=smoke" \
    -c "schedule_test_expression=${SCHEDULE_TEST_EXPRESSION}"
cd "${REPO_ROOT}"

# 0d-5: Render real runtime config from CFN outputs -- runs in container
log "Rendering real runtime config from CloudFormation outputs"
docker_run_aws "${RESULTS_DIR}/phase0_config_render_from_outputs.log" -- \
    config-render-from-outputs \
    --stack "${STACK_NAME}" \
    --region "${REGION}" \
    --deployment configs/deployments/aws-smoke.yaml \
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
    --deployment configs/deployments/aws-smoke.yaml \
    --output configs/generated/aws-smoke.runtime.yaml

# 0d-6: ECR push (host -- requires docker login)
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
