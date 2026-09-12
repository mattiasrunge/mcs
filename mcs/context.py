"""What every op needs a handle on: settings, roots, admission, the worker, the tools."""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field

from .admission import Admission
from .config import Settings
from .roots import Roots
from .tools.audio import AudioCache
from .tools.exiftool import ExifTool
from .worker import Worker


@dataclass
class Context:
    settings: Settings
    roots: Roots
    admission: Admission
    worker: Worker
    exiftool: ExifTool | None
    audio: AudioCache
    started_at: float = field(default_factory=time.time)
    warnings: list[str] = field(default_factory=list)

    def scratch_dir(self, prefix: str) -> str:
        os.makedirs(self.settings.scratch, exist_ok=True)
        return tempfile.mkdtemp(prefix=f"{prefix}-", dir=self.settings.scratch)
