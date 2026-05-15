"""Unit tests for archward.config.paths — profile-name validation and path resolution."""

from __future__ import annotations

import pytest

from archward.config import paths


class TestValidProfileName:
    @pytest.mark.parametrize(
        "name",
        [
            "a",
            "A",
            "0",
            "default",
            "ci",
            "lab-vm",
            "lab_vm",
            "Profile-1_test",
            "abc123",
            "x" * 64,  # max length
        ],
    )
    def test_accepts_safe_names(self, name: str) -> None:
        assert paths.valid_profile_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",                   # empty
            ".hidden",            # leading dot — could be confused with dotfiles
            "_leading-underscore",  # leading underscore rejected (must start alnum)
            "-leading-dash",      # leading dash rejected
            "has space",          # whitespace
            "has/slash",          # path separator
            "../escape",          # path traversal
            "..",                 # path traversal
            ".",                  # current dir
            "name.toml",          # dot anywhere
            "name$injection",     # shell metacharacter
            "name;rm -rf",        # command injection
            "name`whoami`",       # command substitution
            "name|pipe",
            "name&background",
            'name"quote',
            "name'quote",
            "name\\backslash",
            "name\nnewline",
            "name\ttab",
            "x" * 65,             # one over max length
        ],
    )
    def test_rejects_unsafe_names(self, name: str) -> None:
        assert paths.valid_profile_name(name) is False


class TestProfileConfigPath:
    def test_returns_path_under_profile_dir(self) -> None:
        result = paths.profile_config_path("daily")
        assert result == paths.profile_dir() / "daily.toml"

    def test_path_stays_under_profile_dir(self) -> None:
        # Resolve to absolute paths and assert containment — defense in depth
        # in case the regex ever loosens.
        result = paths.profile_config_path("test").resolve()
        profile_root = paths.profile_dir().resolve()
        assert str(result).startswith(str(profile_root) + "/")

    @pytest.mark.parametrize(
        "name",
        [
            "../escape",
            "foo/bar",
            ".hidden",
            "",
            "has space",
            "x" * 65,
        ],
    )
    def test_rejects_invalid_names_with_value_error(self, name: str) -> None:
        with pytest.raises(ValueError, match="invalid profile name"):
            paths.profile_config_path(name)


class TestProfileDir:
    def test_under_config_dir(self) -> None:
        assert paths.profile_dir() == paths.config_dir() / "profiles"


class TestIterProfiles:
    """iter_profiles() scans profile_dir(). Tests redirect that dir via the
    XDG_CONFIG_HOME env var so they don't touch the user's real ~/.config."""

    def test_no_dir_returns_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # profile_dir() = tmp_path/archward/profiles; it does not exist
        assert paths.iter_profiles() == []

    def test_empty_dir_returns_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        paths.profile_dir().mkdir(parents=True)
        assert paths.iter_profiles() == []

    def test_populated_dir_sorted(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        pdir = paths.profile_dir()
        pdir.mkdir(parents=True)
        # Created in non-alpha order; iter_profiles() should sort.
        (pdir / "zebra.toml").write_text("schema_version = 1\n")
        (pdir / "alpha.toml").write_text("schema_version = 1\n")
        (pdir / "mid-name.toml").write_text("schema_version = 1\n")
        assert paths.iter_profiles() == ["alpha", "mid-name", "zebra"]

    def test_ignores_invalid_names(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        pdir = paths.profile_dir()
        pdir.mkdir(parents=True)
        # Valid neighbor — should appear.
        (pdir / "good.toml").write_text("")
        # Leading dot — rejected by valid_profile_name even though it's a
        # well-formed Path. Tests our defense-in-depth filter.
        (pdir / ".hidden.toml").write_text("")
        # Leading dash — rejected.
        (pdir / "-leading.toml").write_text("")
        assert paths.iter_profiles() == ["good"]

    def test_ignores_non_toml_files(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        pdir = paths.profile_dir()
        pdir.mkdir(parents=True)
        (pdir / "real.toml").write_text("")
        (pdir / "backup.toml.bak").write_text("")
        (pdir / "README").write_text("")
        assert paths.iter_profiles() == ["real"]
