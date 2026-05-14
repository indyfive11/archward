"""pacman_argv carries --ignore flags through correctly."""

from __future__ import annotations

from archward.pacman.runner import pacman_argv


def test_empty_ignore_list_produces_no_ignore_flag() -> None:
    argv = pacman_argv(extra=[], noconfirm=True, ignore=[])
    assert "--ignore" not in argv


def test_single_package_ignore() -> None:
    argv = pacman_argv(extra=[], noconfirm=True, ignore=["linux"])
    # Order: pacman -Syu output-flags --noconfirm [--ignore linux] [extras]
    assert "--ignore" in argv
    idx = argv.index("--ignore")
    assert argv[idx + 1] == "linux"


def test_multiple_packages_each_get_their_own_ignore_pair() -> None:
    """pacman accepts repeated --ignore=<pkg> flags."""
    argv = pacman_argv(
        extra=[],
        noconfirm=True,
        ignore=["linux", "linux-headers", "glibc"],
    )
    pairs = []
    for i, tok in enumerate(argv):
        if tok == "--ignore":
            pairs.append(argv[i + 1])
    assert pairs == ["linux", "linux-headers", "glibc"]


def test_ignore_appears_before_extra_args() -> None:
    """Extra args go at the end so the user's flags can override anything we set."""
    argv = pacman_argv(
        extra=["--needed"],
        noconfirm=True,
        ignore=["linux"],
    )
    ignore_idx = argv.index("--ignore")
    needed_idx = argv.index("--needed")
    assert ignore_idx < needed_idx
