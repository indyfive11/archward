from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def setup_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    """Set up the root logger with a rotating file handler. Returns the active log path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "archward.log"

    root = logging.getLogger()
    root.setLevel(level)

    # Clear pre-existing handlers (matters for repeat invocations in tests).
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(console)

    return log_path
