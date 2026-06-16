"""Auto-register built-in ingestion source modules."""

from pathlib import Path
import importlib


package_dir = Path(__file__).parent

for path in package_dir.glob("*.py"):
    if path.stem.startswith("_"):
        continue

    if path.stem == "__init__":
        continue

    importlib.import_module(
        f"{__name__}.{path.stem}"
    )
