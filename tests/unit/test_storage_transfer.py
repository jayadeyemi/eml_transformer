import gzip
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from eml_transformer.storage.local import LocalStorage
from eml_transformer.storage.transfer import TransferAggregator


class TransferAggregatorTests(unittest.TestCase):
    def test_json_aggregation_writes_compressed_parts_and_manifest(self):
        with TemporaryDirectory() as tmp:
            storage = LocalStorage(base_dir=Path(tmp))
            storage.write_json({"id": "a", "text": "one"}, "bronze/articles/a.json")
            storage.write_jsonl(
                "bronze/articles/b.jsonl",
                [
                    {"id": "b", "text": "two"},
                    {"id": "c", "text": "three"},
                ],
            )

            result = TransferAggregator(storage).aggregate_json_records(
                source_prefix="bronze/articles/",
                name="articles",
                run_id="run-1",
                target_rows=2,
            )

            self.assertEqual(result.input_files, 2)
            self.assertEqual(result.records, 3)
            self.assertEqual(len(result.parts), 2)
            self.assertTrue(storage.exists(result.manifest_key))

            rows = []
            for part in result.parts:
                payload = gzip.decompress(storage.read_bytes(part.key)).decode("utf-8")
                rows.extend(json.loads(line) for line in payload.splitlines())

            manifest = storage.read_json(result.manifest_key)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["source_key"], "bronze/articles/a.json")
        self.assertEqual(rows[0]["payload"]["id"], "a")
        self.assertEqual(manifest["records"], 3)
        self.assertEqual(manifest["aggregate_format"], "jsonl.gz")

    def test_parquet_aggregation_compacts_tables_and_manifest(self):
        with TemporaryDirectory() as tmp:
            storage = LocalStorage(base_dir=Path(tmp))
            storage.write_parquet(
                pd.DataFrame([{"record_id": "r1"}, {"record_id": "r2"}]),
                "silver/source=gdelt/part-1.parquet",
            )
            storage.write_parquet(
                pd.DataFrame([{"record_id": "r3"}]),
                "silver/source=gdelt/part-2.parquet",
            )

            result = TransferAggregator(storage).aggregate_parquet_records(
                source_prefix="silver/source=gdelt/",
                name="gdelt-silver",
                run_id="run-1",
                target_rows=10,
            )

            self.assertEqual(result.input_files, 2)
            self.assertEqual(result.records, 3)
            self.assertEqual(len(result.parts), 1)
            df = storage.read_parquet(result.parts[0].key)
            manifest = storage.read_json(result.manifest_key)

        self.assertEqual(set(df["record_id"]), {"r1", "r2", "r3"})
        self.assertIn("_transfer_source_key", df.columns)
        self.assertEqual(manifest["aggregate_format"], "parquet")


if __name__ == "__main__":
    unittest.main()
