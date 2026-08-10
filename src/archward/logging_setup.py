from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def setup_logging(log_dir: Path, level: int = logging.INFO, keep_logs: int = 20) -> Path:
    """Set up the root logger with a rotating file handler. Returns the active log path.

    `keep_logs` is the user's general.keep_logs setting — the number of
    rotated log files retained.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "archward.log"

    root = logging.getLogger()
    root.setLevel(level)

    # Clear pre-existing handlers (matters for repeat invocations in tests).
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=max(1, keep_logs)
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(console)

    return log_path
