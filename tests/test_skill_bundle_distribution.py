"""Verify the sleep-cycle skill bundle ships in PyPI distribution.

Stage 3 Sprint 1 packaging: the graphify/sleep-cycle/ directory tree must
be included in both the sdist (via MANIFEST.in) and the wheel (via
pyproject.toml [tool.setuptools.package-data]).
"""
import importlib.resources
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    """Source bundle dir inside the package tree."""
    return REPO_ROOT / "graphify" / "sleep-cycle"


def test_bundle_source_exists():
    """The bundle source tree exists at graphify/sleep-cycle/ in the repo."""
    bd = _bundle_dir()
    assert bd.is_dir(), f"missing bundle dir: {bd}"
    assert (bd / "SKILL.md").is_file()
    assert (bd / "README.md").is_file()
    assert (bd / "templates").is_dir()
    assert (bd / "templates" / "safety_gates.md").is_file()
    assert (bd / "templates" / "sleep_1_replay.md").is_file()
    assert (bd / "templates" / "sleep_2_nrem.md").is_file()


def test_importlib_resources_finds_bundle():
    """importlib.resources.files('graphify') / 'sleep-cycle' resolves at runtime."""
    src = importlib.resources.files("graphify") / "sleep-cycle"
    # Traversable check: .is_dir() works on both packaged and source layouts
    assert src.is_dir(), f"importlib.resources cannot find bundle: {src}"
    assert (src / "SKILL.md").is_file()
    assert (src / "templates" / "sleep_1_replay.md").is_file()


def test_manifest_in_exists_and_includes_bundle():
    """MANIFEST.in includes the sleep-cycle tree via `graft`."""
    mi = REPO_ROOT / "MANIFEST.in"
    assert mi.is_file(), "MANIFEST.in must exist at repo root"
    content = mi.read_text()
    assert "graft graphify/sleep-cycle" in content, (
        "MANIFEST.in must `graft graphify/sleep-cycle`"
    )


def test_pyproject_package_data_includes_bundle():
    """pyproject.toml [tool.setuptools.package-data] graphify list includes sleep-cycle paths."""
    pj = (REPO_ROOT / "pyproject.toml").read_text()
    # All 3 entries must be listed
    assert '"sleep-cycle/SKILL.md"' in pj, "missing sleep-cycle/SKILL.md in package-data"
    assert '"sleep-cycle/README.md"' in pj, "missing sleep-cycle/README.md in package-data"
    assert '"sleep-cycle/templates/*.md"' in pj, (
        "missing sleep-cycle/templates/*.md in package-data"
    )


@pytest.mark.skipif(
    not shutil.which(sys.executable),
    reason="python executable not found",
)
def test_sdist_contains_bundle(tmp_path):
    """`python -m build --sdist` produces a tarball containing the bundle.

    Canonical end-to-end packaging test: confirms that an `pip install graphifyy`
    from PyPI would find the bundle via `importlib.resources`.
    """
    try:
        import build  # noqa: F401
    except ImportError:
        pytest.skip("'build' package not installed; install with `uv pip install build`")
        return
    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(tmp_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"build --sdist failed:\n{result.stderr}"
    # Package name is `tecnoandina-graphify` (renamed from upstream `graphifyy`)
    sdists = list(tmp_path.glob("tecnoandina*graphify-*.tar.gz"))
    if not sdists:
        # Fallback to legacy name if rebuilding upstream
        sdists = list(tmp_path.glob("graphifyy-*.tar.gz"))
    assert sdists, f"no sdist produced in {tmp_path}"
    sdist = sdists[0]
    with tarfile.open(sdist) as tf:
        names = tf.getnames()
    expected = [
        "graphify/sleep-cycle/SKILL.md",
        "graphify/sleep-cycle/README.md",
        "graphify/sleep-cycle/templates/safety_gates.md",
        "graphify/sleep-cycle/templates/sleep_1_replay.md",
        "graphify/sleep-cycle/templates/sleep_2_nrem.md",
    ]
    for path in expected:
        assert any(n.endswith(path) for n in names), (
            f"sdist missing {path!r}; first 20 entries: {names[:20]}"
        )
