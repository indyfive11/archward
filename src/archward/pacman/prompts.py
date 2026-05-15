"""Pacman/AUR interactive prompt detection.

The PTY-based runner in `pacman.runner` reads stdout into a line buffer.
Between newlines, when the read goes idle (pacman/yay/paru is waiting on
stdin), the buffer is matched against PROMPT_PATTERNS. A match yields a
PromptKind, which the GUI uses to pre-fill a sensible default for the
inline input row in UpdateView.

KISS: this is a small fixed set of regexes — when pacman releases tweak
wording the unmatched prompt just stalls the log, the user can cancel
and re-run with noconfirm=True. Add new patterns here as they appear.
"""

from __future__ import annotations

import re
from enum import Enum, auto


class PromptKind(Enum):
    YES_NO = auto()   # default: Y
    NUMERIC = auto()  # default: 1
    FREE = auto()     # no default (rare; we still show the input row)


# Pattern table is checked top-to-bottom; first match wins.
PROMPT_PATTERNS: tuple[tuple[re.Pattern[str], PromptKind], ...] = (
    (re.compile(r":: Proceed with installation\?\s*\[Y/n\]\s*$"),       PromptKind.YES_NO),
    (re.compile(r":: Replace [^?]+\?\s*\[Y/n\]\s*$"),                    PromptKind.YES_NO),
    (re.compile(r":: Import PGP key [^?]+\?\s*\[Y/n\]\s*$"),             PromptKind.YES_NO),
    (re.compile(r":: There are \d+ providers available for [^:]+:\s*$"), PromptKind.NUMERIC),
    (re.compile(r":: Enter a selection \(default=\d+\):\s*$"),           PromptKind.NUMERIC),
    (re.compile(r"\[Y/n\]\s*$"),                                          PromptKind.YES_NO),
    (re.compile(r"\[y/N\]\s*$"),                                          PromptKind.YES_NO),
    # yay/paru sometimes ask "Enter a selection or packages to clean (eg: 1 2 3):" — free text
    (re.compile(r":: Enter [a-z ]*packages?[^:]*\(eg:[^)]*\):\s*$"),      PromptKind.FREE),
)


def detect_prompt(buffered_line: str) -> PromptKind | None:
    """Return the PromptKind if the buffered (newline-less) line looks like a
    pacman/AUR-helper prompt, else None.

    The buffer is the partial line accumulated since the last `\\n`. We strip
    a trailing space tolerance but match against the line as the subprocess
    emitted it — pacman's prompts always end at column-width or near it.
    """
    if not buffered_line:
        return None
    line = buffered_line.rstrip("\r")
    for pattern, kind in PROMPT_PATTERNS:
        if pattern.search(line):
            return kind
    return None


def default_response(kind: PromptKind) -> str:
    """The keystroke the user gets pre-filled by the GUI input row."""
    if kind is PromptKind.YES_NO:
        return "Y"
    if kind is PromptKind.NUMERIC:
        return "1"
    return ""
