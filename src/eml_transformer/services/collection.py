from __future__ import annotations

from typing import Any

from eml_transformer.pipelines.backfill_pipeline import BackfillPipeline
from eml_transformer.pipelines.ingestion_pipeline import IngestionPipeline
from eml_transformer.pipelines.standardization_pipeline import StandardizationPipeline
from eml_transformer.runtime import build_runtime


class CollectionServiceRunner:
    """
    Thin microservice adapter around the existing pipeline classes.

    This keeps AWS Batch/ECS commands aligned with local CLI behavior instead of
    maintaining a separate cloud-only implementation.
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.runtime = build_runtime(config_path)

    def ingest(self, source: str = "all") -> list[dict[str, Any]]:
        pipeline = IngestionPipeline(
            storage=self.runtime.storage,
            paths=self.runtime.paths,
        )

        if source.lower() == "all":
            results = pipeline.run_all(self.runtime.source_configs)
        else:
            results = [
                pipeline.run_source(
                    source,
                    self._source_config(source),
                )
            ]

        return [result.to_summary() for result in results]

    def standardize(self, source: str = "all") -> list[dict[str, Any]]:
        pipeline = StandardizationPipeline(
            storage=self.runtime.storage,
            paths=self.runtime.paths,
        )

        if source.lower() == "all":
            results = pipeline.run_all(self.runtime.source_configs)
        else:
            results = [
                pipeline.run_source(
                    source,
                    self._source_config(source),
                )
            ]

        return [result.to_summary() for result in results]

    def embed(
        self,
        source: str = "all",
        model_name: str | None = None,
    ) -> list[dict[str, Any]]:
        from eml_transformer.pipelines.embedding_pipeline import EmbeddingPipeline

        embedding_config = dict(self.runtime.embedding_config)

        if model_name is not None:
            embedding_config["model"] = model_name

        pipeline = EmbeddingPipeline(
            storage=self.runtime.storage,
            paths=self.runtime.paths,
        )

        if source.lower() == "all":
            results = pipeline.run_all(
                embedding_config=embedding_config,
                source_configs=self.runtime.source_configs,
            )
        else:
            self._source_config(source)
            results = [
                pipeline.run_source(
                    source=source,
                    embedding_config=embedding_config,
                )
            ]

        return [result.to_summary() for result in results]

    def backfill(
        self,
        source: str,
        start_date: str,
        end_date: str,
        window_days: int = 30,
        init_checkpoint: bool = False,
    ) -> list[dict[str, Any]]:
        ingestion_pipeline = IngestionPipeline(
            storage=self.runtime.storage,
            paths=self.runtime.paths,
        )
        pipeline = BackfillPipeline(ingestion_pipeline=ingestion_pipeline)

        if source.lower() == "all":
            results = pipeline.run_all(
                source_configs=self.runtime.source_configs,
                start_date=start_date,
                end_date=end_date,
                window_days=window_days,
                seed_checkpoint=init_checkpoint,
            )
        else:
            results = [
                pipeline.run_source(
                    source_name=source,
                    source_config=self._source_config(source),
                    start_date=start_date,
                    end_date=end_date,
                    window_days=window_days,
                    seed_checkpoint=init_checkpoint,
                )
            ]

        return self._summarize_results(results)

    def run_all(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "ingestion": self.ingest(source="all"),
            "standardization": self.standardize(source="all"),
            "embedding": self.embed(source="all"),
        }

    def run(
        self,
        service: str,
        source: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        window_days: int = 30,
        model_name: str | None = None,
        init_checkpoint: bool = False,
    ) -> dict[str, Any]:
        service_name = service.strip().lower().replace("-", "_")

        if service_name == "ingest":
            return {"service": service_name, "results": self.ingest(source=source)}

        if service_name == "standardize":
            return {"service": service_name, "results": self.standardize(source=source)}

        if service_name == "embed":
            return {
                "service": service_name,
                "results": self.embed(source=source, model_name=model_name),
            }

        if service_name == "backfill":
            if not start_date or not end_date:
                raise ValueError("backfill requires start_date and end_date")

            return {
                "service": service_name,
                "results": self.backfill(
                    source=source,
                    start_date=start_date,
                    end_date=end_date,
                    window_days=window_days,
                    init_checkpoint=init_checkpoint,
                ),
            }

        if service_name == "run_all":
            return {"service": service_name, "results": self.run_all()}

        raise ValueError(f"Unknown collection service: {service}")

    def _source_config(self, source: str) -> dict[str, Any]:
        if source not in self.runtime.source_configs:
            available = ", ".join(sorted(self.runtime.source_configs))
            raise ValueError(
                f"Unknown source: {source}. Available sources: {available}"
            )

        return self.runtime.source_configs[source]

    def _summarize_results(self, results: Any) -> list[dict[str, Any]]:
        if results is None:
            return []

        if isinstance(results, dict):
            if not any(
                isinstance(value, (dict, list, tuple)) or hasattr(value, "to_summary")
                for value in results.values()
            ):
                return [results]

            rows: list[dict[str, Any]] = []
            for value in results.values():
                rows.extend(self._summarize_results(value))
            return rows

        if isinstance(results, (list, tuple)):
            rows = []
            for value in results:
                rows.extend(self._summarize_results(value))
            return rows

        if hasattr(results, "to_summary"):
            return [results.to_summary()]

        return [{"result": str(results)}]
