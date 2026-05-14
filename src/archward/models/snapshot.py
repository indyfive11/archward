from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict


class SnapshotMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    created_at: datetime
    path: Path
    distro_id: str
    kernel_release: str
    free_disk_gb: int
    helper_detected: str | None = None


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    meta: SnapshotMeta
    package_files: Mapping[str, Path]
    config_files: tuple[Path, ...]
    service_files: Mapping[str, Path]
    age_seconds: int
