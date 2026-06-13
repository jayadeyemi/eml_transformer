# EML Transformer

Pipeline for ingesting and processing energy-related textual data
for NLP-driven load forecasting research.


# Overview

This project collects and standardizes textual data sources such as:

- MISO notifications
- NewsAPI articles
- National Weather Service alerts



# Quick Start

## 1. Clone Repository

```bash
git clone https://github.com/jackyeung99/eml_transformer.git
cd eml_transformer
```

---

## 2. (Optional) Create Virtual Environment

Creating a virtual environment is recommended to avoid dependency conflicts.

### Mac/Linux

```bash
python -m venv .venv
source .venv/bin/activate
```

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
```

---

## 3. Install Dependencies

Run the setup command:

```bash
make setup
```

This installs:
- project dependencies
- editable package installation
- required development tools

If `make` is unavailable on your system, run:

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` installs the local package with the AWS runtime extra. For
embedding/modeling work, install the optional HPC dependencies separately:

```bash
python -m pip install -e .[hpc]
```

---

## 4. Configure Environment Variables

Create a `.env` file in the repository root:

```env
NEWSAPI_KEY=your_key_here
```


# Running the Pipeline

List all available sources 

```bash
eml_transformer sources
```

## Ingestion

Run all sources:

```bash
eml_transformer ingest --source all
```

Run NewsAPI ingestion:

```bash
eml_transformer ingest --source newsapi
```

Run Weather Alerts ingestion:

```bash
eml_transformer ingest --source weather_alerts
```

Run MISO Notifications ingestion:

```bash
eml_transformer ingest --source miso_notifications
```

---

## Standardization

Standardize all sources:

```bash
eml_transformer standardize --source all
```

Standardize a single source:

```bash
eml_transformer standardize --source newsapi
```

---

## Embeddings

Generate embeddings using NVIDIA NeMo Retriever NIM embedding models for scalable GPU-accelerated inference.

```bash
eml_transformer embed \
    --model nvidia/nv-embedqa-e5-v5
```


## Backfilling Historical Data 

Some api sources archive historical data. To back fill historical data run 

```bash
eml_transformer backfill   --source newsapi   --start-date 2026-04-20   --end-date 2026-05-20   --window-days 7
```

** this command is limited to data sources with supports_backfill=True and is also rate limited depending on source 

# Output Structure
The textual ingestion pipeline is built around a medallion architecture.
Local runs normally write under `data/`; AWS deployment configs currently use
`paths.root: .`, so S3 keys start at `bronze/`, `silver/`, `gold/`, and
`metadata/`.


### Bronze Layer

Raw API responses.

```text
data/bronze/source=<source>/records.jsonl
```

In S3, appended generic source rows may be stored in companion part files under
`bronze/source=<source>/records.jsonl.parts/`; the S3 reader loads the marker
object and all part files.

### Silver Layer

Cleaned and standardized records.

```text
data/silver/source=<source>/records.parquet
```

### Gold Layer 
text embeddings 

```text 
data/gold/model=<model>/source=<source>/embeddings.parquet
```

For AWS-specific GDELT, article fetch, manifest, restore, and lifecycle paths,
see `docs/aws_s3_layout.md`.

# Documentation

Detailed documentation can be found in:

```text
docs/
```

Important guides:

- `docs/design_principles.md`
- `docs/project_structure.md`
- `docs/ingestion_pipeline.md`
- `docs/aws_s3_layout.md`
- `docs/aws_deployment_security.md`
