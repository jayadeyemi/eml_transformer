# EML Transformer

Pipeline for ingesting and processing energy-related textual data
for NLP-driven load forecasting research.


# Overview

This project collects and standardizes textual data sources such as:

- MISO notifications
- NewsAPI articles
- National Weather Service alerts

The pipeline supports:

- Incremental ingestion
- Deduplication
- Bronze/Silver storage layers
- Embedding-ready outputs
- Research workflows for energy forecasting


# Repository Structure

```text
eml_transformer/
├── docs/
├── src/
├── configs/
├── data/
│   ├── bronze/
│   ├── silver/
│   └── gold/
├── scripts/
└── README.md
```


# Quick Start

## 1. Clone Repository

```bash
git clone ...
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


Run all sources
```bash
eml_transformer --ingest all 
```

News
```bash
eml_transformer --ingest news
```

Weather Alerts

```bash
eml_transformer --ingest weather
```

MISO Notifications

```bash
eml_transformer --ingest miso
```

# Output Structure

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
text pre processing and embeddings 

```text 
data/gold/
```

# Documentation

Detailed documentation can be found in:

```text
docs/
```

Important guides:

- `docs/ingestion_pipeline.md`
- `docs/add_new_source.md`
- `docs/architecture` philosophy behind code architecture

