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
python -m pip install -e .
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
The textual ingestion pipeline is built around the medallion architecture with the following data design structure 


### Bronze Layer

Raw API responses.

```text
data/bronze/source=
```

### Silver Layer

Cleaned and standardized records.

```text
data/silver/source=
```

### Gold Layer 
text embeddings 

```text 
data/gold/
```

# Documentation

Detailed documentation can be found in:

```text
docs/
```

Important guides:

- `docs/design_principles.md`
- `docs/project_structure` 
- `docs/ingestion_pipeline.md`

