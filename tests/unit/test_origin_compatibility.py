import unittest
from pathlib import Path

from typer.testing import CliRunner

from eml_transformer.cli import app
from eml_transformer.runtime import build_runtime
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
