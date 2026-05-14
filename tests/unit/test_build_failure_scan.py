"""Build failure scanner — attributes errors to package context."""

from __future__ import annotations

from archward.pipeline.update_aur import scan_build_failures


def test_no_failures_in_clean_output() -> None:
    captured = [
        ":: building radarr",
        "==> Making package: radarr 5.28.0",
        "==> Retrieving sources...",
        "==> Building...",
        "==> Finished",
    ]
    assert scan_build_failures(captured) == []


def test_detects_makepkg_error() -> None:
    captured = [
        "==> Making package: radarr 5.28.0",
        "==> Retrieving sources...",
        "==> Validating source files",
        "ERROR: dependency NETFRAMEWORK was not satisfied",
        "==> ERROR: Could not resolve all dependencies.",
        "==> Build of radarr failed",
    ]
    failures = scan_build_failures(captured)
    assert len(failures) == 1
    assert failures[0].package == "radarr"
    # Last lines include the ERROR context.
    assert any("==> ERROR:" in line for line in failures[0].last_lines)


def test_one_failure_per_package() -> None:
    """Multiple ERROR lines for the same package collapse to one BuildFailure."""
    captured = [
        "==> Making package: radarr 5.28.0",
        "==> ERROR: build step A failed",
        "==> ERROR: build step B failed",
        "==> Build of radarr failed",
    ]
    failures = scan_build_failures(captured)
    assert len(failures) == 1
    assert failures[0].package == "radarr"


def test_multiple_packages_each_get_their_own_failure() -> None:
    captured = [
        "==> Making package: pkg-a",
        "==> ERROR: build failed for pkg-a",
        "==> Making package: pkg-b",
        "==> Retrieving sources",
        "==> Finished",
        "==> Making package: pkg-c",
        "failed to build pkg-c — missing dependency",
    ]
    failures = scan_build_failures(captured)
    assert len(failures) == 2
    pkgs = {f.package for f in failures}
    assert pkgs == {"pkg-a", "pkg-c"}


def test_error_before_package_context_attributes_to_unknown() -> None:
    captured = [
        "==> ERROR: something went wrong",
    ]
    failures = scan_build_failures(captured)
    assert len(failures) == 1
    assert failures[0].package == "(unknown)"


def test_tail_lines_truncated_to_50() -> None:
    captured = [f"line {i}" for i in range(100)]
    captured[50] = "==> Making package: foo"
    captured[99] = "==> ERROR: failed"
    failures = scan_build_failures(captured, tail_lines=50)
    assert len(failures) == 1
    assert len(failures[0].last_lines) <= 50
    # The ERROR line is included as the last captured line.
    assert "==> ERROR: failed" in failures[0].last_lines[-1]
