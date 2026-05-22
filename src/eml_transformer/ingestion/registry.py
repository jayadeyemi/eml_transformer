from __future__ import annotations

from typing import Any, Type

from eml_transformer.ingestion.base import TextSource


_SOURCE_REGISTRY: dict[str, Type[TextSource]] = {}


def register_source(name: str):
    def decorator(cls):
        if name in _SOURCE_REGISTRY:
            old_cls = _SOURCE_REGISTRY[name]

            # allow notebook/autoreload re-registration of same source name
            if old_cls.__name__ == cls.__name__:
                _SOURCE_REGISTRY[name] = cls
                return cls

            raise ValueError(f"Source already registered: {name}")

        _SOURCE_REGISTRY[name] = cls
        return cls

    return decorator


def create_source(name: str, **kwargs: Any) -> TextSource:
    """
    Instantiate a registered source by name.
    """
    if name not in _SOURCE_REGISTRY:
        available = ", ".join(sorted(_SOURCE_REGISTRY))
        raise ValueError(
            f"Unknown source: {name}. Available sources: {available}"
        )

    source_cls = _SOURCE_REGISTRY[name]
    return source_cls(**kwargs)


def available_sources() -> list[str]:
    """
    Return all registered source names.
    """
    return sorted(_SOURCE_REGISTRY)