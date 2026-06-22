# Storage And Transfer Aggregation

This project should keep one logical medallion layout across local, SLATE/HPC,
and S3 storage:

```text
bronze/
silver/
gold/
metadata/
manifests/
```

The storage backend changes by runtime:

- Local/HPC/SLATE: `storage.backend: local`, with `storage.base_dir` pointing at
  the local or shared filesystem root.
- AWS: `storage.backend: s3`, with `storage.bucket` and optional
  `storage.prefix`.

For SLATE, prefer setting `SLATE_DATA_DIR`, `HPC_DATA_DIR`, or
`STORAGE_BASE_DIR` instead of changing committed paths.

## Ideal File Types

Use file formats according to access pattern:

| Layer | Preferred format | Reason |
| --- | --- | --- |
| Bronze API records | JSONL or JSONL gzip parts | Append-friendly, source-preserving, easy to stream. |
| Bronze raw GDELT files | Original `.zip` files | Avoids lossy conversion and supports cache reuse. |
| Bronze fetched articles | Batched `jsonl.gz` | Avoids one-small-file-per-URL transfer overhead. |
| Silver records/articles | Parquet | Columnar, compact, efficient for analytics and embeddings. |
| Gold embeddings/features | Parquet | Efficient array/table storage and model partitioning. |
| Metadata/checkpoints/manifests | Small JSON | Human-readable operational state. |
| Cross-platform transfer bundles | `jsonl.gz` or compacted Parquet | Fewer, larger files copy more reliably between filesystems. |

Do not treat transfer bundles as the source of truth. They are generated
artifacts for moving data between platforms.

## Transfer Aggregates

High-compute environments such as HPC/SLATE should aggregate small files before
copying them to another filesystem. This avoids thousands of small copy
operations and keeps transfer verification manifest-based.

JSON/JSONL aggregation:

```bash
eml_transformer storage-aggregate-transfer \
  --config configs/hpc.yaml \
  --source-prefix bronze/articles/batches/ \
  --name article-batches \
  --input-format json \
  --target-rows 50000
```

Parquet compaction:

```bash
eml_transformer storage-aggregate-transfer \
  --config configs/hpc.yaml \
  --source-prefix silver/source=gdelt/ \
  --name gdelt-silver \
  --input-format parquet \
  --target-rows 250000
```

The command writes aggregate parts under:

```text
transfer/aggregates/name=<name>/run_id=<run_id>/
```

It also writes a manifest under:

```text
manifests/transfer_aggregates/name=<name>/run_id=<run_id>.json
```

Copy the aggregate parts and manifest, not each original small file, when moving
data across SLATE, local scratch, and S3.
