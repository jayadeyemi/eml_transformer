FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ARG OPTIONAL_EXTRAS=aws
ARG INSTALL_CDK_TOOLING=0

COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY tests ./tests
COPY infra/cdk ./infra/cdk

RUN if [ "${INSTALL_CDK_TOOLING}" = "1" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends nodejs npm \
        && npm install -g aws-cdk \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/*; \
    fi

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir ".[${OPTIONAL_EXTRAS}]"

ENTRYPOINT ["eml_transformer"]
CMD ["--help"]
