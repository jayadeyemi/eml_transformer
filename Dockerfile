FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ARG OPTIONAL_EXTRAS=aws

COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY tests ./tests

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir ".[${OPTIONAL_EXTRAS}]"

ENTRYPOINT ["eml_transformer"]
CMD ["--help"]
