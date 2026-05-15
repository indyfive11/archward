"""CLI subcommand modules (v0.4.3).

Each module exposes one or more `cmd_*` entry points called by
`archward.cli._dispatch_subcommand`. Each function is responsible for
loading config + (when needed) building the sudo strategy, printing its
own output, and returning a Unix-style exit code:

    0 — success
    1 — operation failed (verify FAIL, pacman -U non-zero, etc.)
    2 — invalid args or refused (boot-critical without --confirm-...)
    3 — snapshot not found

Subcommand modules MUST NOT import any GUI (PySide6) modules — the CLI
is the recovery path when the GUI can't run.
"""
