import json 

from typing import Any
import hashlib

def stable_hash(obj: dict[str, Any]) -> str:
    payload = json.dumps(
        obj,
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()