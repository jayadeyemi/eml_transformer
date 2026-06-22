import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
from typer.testing import CliRunner

from eml_transformer.cli import app
from eml_transformer.runtime import build_runtime
from eml_transformer.pipelines.embedding_pipeline import EmbeddingPipeline
from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import LocalStorage, Storage, make_storage
from eml_transformer.utils.config import DEFAULT_RUNTIME_CONFIG


class OriginCompatibilityTests(unittest.TestCase):
    def test_original_default_config_path_exists(self):
        self.assertEqual(DEFAULT_RUNTIME_CONFIG, "configs/dev.yaml")
        self.assertTrue(Path(DEFAULT_RUNTIME_CONFIG).exists())

    def test_original_storage_import_path_exports_core_symbols(self):
        storage = make_storage({"backend": "local", "base_dir": "data"})
        self.assertIsInstance(storage, LocalStorage)
        self.assertIsInstance(storage, Storage)

    def test_original_runtime_builds_from_dev_config(self):
        runtime = build_runtime("configs/dev.yaml")

        self.assertIn("iem_afos", runtime.source_configs)
        self.assertEqual(runtime.storage.base_dir, Path("data"))

    def test_slate_storage_base_dir_can_be_set_by_environment(self):
        with patch.dict("os.environ", {"SLATE_DATA_DIR": "/N/slate/eml/data"}):
            runtime = build_runtime("configs/hpc.yaml")

        self.assertEqual(runtime.storage.base_dir, Path("/N/slate/eml/data"))

    def test_silver_paths_support_existing_named_artifacts(self):
        paths = StoragePaths(root="data")

        self.assertEqual(
            paths.silver_records("gdelt", name="articles"),
            "data/silver/source=gdelt/articles.parquet",
        )

    def test_hpc_runtime_keeps_gdelt_embedding_only_compatibility(self):
        runtime = build_runtime("configs/hpc.yaml")

        self.assertNotIn("gdelt", runtime.source_configs)
        self.assertIn("gdelt", runtime.embedding_source_configs)
        self.assertEqual(
            runtime.embedding_source_configs["gdelt"]["embedding_input"],
            "articles",
        )

    def test_embedding_pipeline_can_read_existing_article_artifact(self):
        with TemporaryDirectory() as tmp:
            storage = LocalStorage(Path(tmp))
            paths = StoragePaths(root="data")
            key = paths.silver_records("gdelt", name="articles")
            storage.write_parquet(
                pd.DataFrame(
                    [
                        {
                            "record_id": "r1",
                            "published_at": "2026-06-22T00:00:00+00:00",
                            "title": "Grid outage",
                            "text": "Storm related outage",
                        }
                    ]
                ),
                key,
            )

            pipeline = EmbeddingPipeline(storage=storage, paths=paths)
            df = pipeline._load_source_records(
                "gdelt",
                {"embedding_input": "articles"},
            )

        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["record_id"], "r1")

    def test_original_cli_commands_still_expose_help(self):
        runner = CliRunner()
        commands = [
            ["sources", "--help"],
            ["ingest", "--source", "all", "--config", "configs/dev.yaml", "--help"],
            ["standardize", "--source", "all", "--config", "configs/dev.yaml", "--help"],
            ["embed", "--source", "all", "--config", "configs/dev.yaml", "--help"],
            [
                "backfill",
                "--source",
                "newsapi",
                "--start-date",
                "2026-04-20",
                "--end-date",
                "2026-04-21",
                "--config",
                "configs/dev.yaml",
                "--help",
            ],
        ]

        for command in commands:
            with self.subTest(command=command[0]):
                result = runner.invoke(app, command)
                self.assertEqual(result.exit_code, 0, result.output)


if __name__ == "__main__":
    unittest.main()
