"""Compatibility exports for the original storage import path."""

from eml_transformer.storage.base import Storage
from eml_transformer.storage.factory import make_storage
from eml_transformer.storage.local import LocalStorage
from eml_transformer.storage.s3 import S3Storage


__all__ = ["Storage", "LocalStorage", "S3Storage", "make_storage"]
