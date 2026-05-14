"""User-decision boundary for the pipeline.

run_pipeline asks the user one or two yes/no questions during execution:
HIGH-risk approval, and (optionally) gate-override on a recoverable gate
failure. In CLI mode these are stdin input() calls; in GUI mode they need to
be QMessageBox calls dispatched onto the main thread.

The Prompter Protocol abstracts both. The pipeline only calls prompter
methods; it doesn't know whether the implementation prints to terminal or
pops a modal.
"""

from __future__ import annotations

from typing import Protocol

from archward.models.gate import GateResult
from archward.models.update import PendingUpdate


class Prompter(Protocol):
    """Synchronous decision points the pipeline asks the user about.

    All methods return True for "proceed" and False for "abort". GUI implementations
    block the calling (pipeline) thread until the user answers — typically via
    Qt::BlockingQueuedConnection.
    """

    def confirm_high_risk(self, high: list[PendingUpdate]) -> bool: ...

    def confirm_gate_override(self, gate: GateResult) -> bool: ...


class AutoYesPrompter:
    """Pipeline gets a free pass on every decision. Used by --yes and GUI dry-run."""

    def confirm_high_risk(self, high: list[PendingUpdate]) -> bool:  # noqa: ARG002
        return True

    def confirm_gate_override(self, gate: GateResult) -> bool:  # noqa: ARG002
        return True


class AutoNoPrompter:
    """Pipeline always declines. Used by --auto mode."""

    def confirm_high_risk(self, high: list[PendingUpdate]) -> bool:  # noqa: ARG002
        return False

    def confirm_gate_override(self, gate: GateResult) -> bool:  # noqa: ARG002
        return False


class CliPrompter:
    """input()-based prompter for the terminal."""

    def confirm_high_risk(self, high: list[PendingUpdate]) -> bool:
        try:
            answer = input(
                f"Proceed with update including {len(high)} HIGH RISK package(s)? [y/N] "
            ).strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")

    def confirm_gate_override(self, gate: GateResult) -> bool:
        try:
            answer = input(
                f"Gate {gate.name} failed: {gate.message}. Override? [y/N] "
            ).strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")
