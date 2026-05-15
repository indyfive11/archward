from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from archward.models.pacnew import PacnewRecommendation


class GeneralConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_dir: Path
    keep_snapshots: int = 10
    log_dir: Path
    keep_logs: int = 20
    notify_on_completion: bool = True

    @field_validator("snapshot_dir", "log_dir", mode="before")
    @classmethod
    def _expand_user(cls, v):
        """TOML stores '~/.local/state/...' literally; expand before validation."""
        if isinstance(v, (str, Path)):
            return Path(str(v)).expanduser()
        return v


class GatesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_max_age_minutes: int = 60
    min_disk_gb: int = 5
    allow_override: bool = True


class RiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    high: tuple[str, ...]
    medium_patterns: tuple[str, ...]
    kernel_patterns: tuple[str, ...]
    kernel_pattern_exclude: tuple[str, ...] = ()


class ServicesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    to_verify: tuple[str, ...] = ()
    severity: dict[str, str] = Field(default_factory=dict)
    # severity: per-unit override; default "critical" → FAIL, "watch" → WARN
    auto_prune: bool = False
    # auto_prune: when True, the verify phase silently drops stale entries
    # from to_verify and writes the pruned config back to disk. When False
    # (default), stale entries still get a WARN row pointing the user at
    # `archward --detect` for manual confirmation.


class PacnewRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern: str
    strategy: PacnewRecommendation
    note: str | None = None


class PacnewConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    default_strategy: PacnewRecommendation = PacnewRecommendation.REVIEW_NEEDED
    rules: tuple[PacnewRule, ...]


class AurConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    helper_preference: tuple[str, ...] = ("yay", "paru", "aurutils")
    skip: bool = False


class PacmanConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    noconfirm: bool = True
    extra_args: tuple[str, ...] = ()


class VerifyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    reboot_log: str = "/var/log/reboot-recommendation-trigger.log"


class PrivilegeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: str = "auto"  # auto | askpass | pkexec | persistent_sudo
    askpass: str = ""  # override path; default auto-discovers


class HooksConfig(BaseModel):
    """User-configurable shell commands run at pipeline checkpoints.

    pre_update commands run after risk-approval, before pacman -Syu. Any
    non-zero exit logs a warning by default; set fail_pipeline_on_error=true
    to abort the whole pipeline (useful for "verify backup is fresh" hooks
    that should refuse the update if they fail).

    post_verify commands always run after the verify phase regardless of
    success/failure, and never abort the pipeline (the update already ran).
    """

    model_config = ConfigDict(frozen=True)

    pre_update: tuple[str, ...] = ()
    post_verify: tuple[str, ...] = ()
    timeout_seconds: int = 60
    fail_pipeline_on_error: bool = False


class ConfigModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    general: GeneralConfig
    gates: GatesConfig
    risk: RiskConfig
    services: ServicesConfig
    pacnew: PacnewConfig
    aur: AurConfig
    pacman: PacmanConfig
    verify: VerifyConfig
    privilege: PrivilegeConfig
    hooks: HooksConfig = HooksConfig()
