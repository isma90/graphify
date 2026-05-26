# re-export manifest helpers from detect for backwards compatibility
from graphify.detect import (
    save_manifest,
    load_manifest,
    detect_incremental,
    SCHEMA_VERSION,
    _migrate_manifest,
)

__all__ = [
    "save_manifest",
    "load_manifest",
    "detect_incremental",
    "SCHEMA_VERSION",
    "_migrate_manifest",
]
