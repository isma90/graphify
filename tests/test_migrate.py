"""Unit tests for ``graphify.migrate`` — sharing initialisation, path-based
classification suggestions, legacy -> split migration of an existing
``graph.json``, overlay pattern editing, and manifest v1 -> v2 migration.

The module under test is created by another agent; these tests are written
against the Stage 1.2 spec.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_init(root: Path) -> None:
    """Initialise an empty git repo at *root* with a local identity."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )


def _write_minimal_graph_json(path: Path, nodes: list[dict], links: list[dict] | None = None,
                              *, built_at_commit: str | None = None,
                              labels: dict | None = None) -> None:
    """Write a minimal graph.json payload at *path*."""
    payload: dict = {
        "nodes": nodes,
        "links": list(links or []),
    }
    if built_at_commit is not None:
        payload["built_at_commit"] = built_at_commit
    if labels is not None:
        payload["community_labels"] = labels
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# init_sharing
# ---------------------------------------------------------------------------


def test_init_sharing_creates_overlays(tmp_path: Path) -> None:
    """``init_sharing`` on a fresh repo must create both overlay files plus a
    ``graphify-out/.gitignore`` so private artifacts stay out of VCS."""
    from graphify.migrate import init_sharing

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    init_sharing(repo, interactive=False)

    assert (repo / ".graphifyshared").exists(), ".graphifyshared must be created"
    assert (repo / ".graphifyprivate").exists(), ".graphifyprivate must be created"
    assert (repo / "graphify-out" / ".gitignore").exists(), (
        "graphify-out/.gitignore must be created to keep private outputs out of VCS"
    )


def test_init_sharing_idempotent(tmp_path: Path) -> None:
    """Running ``init_sharing`` twice must not overwrite user-edited overlays."""
    from graphify.migrate import init_sharing

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    init_sharing(repo, interactive=False)
    # Simulate a user edit between runs.
    shared = repo / ".graphifyshared"
    shared.write_text("custom/**\n", encoding="utf-8")
    private = repo / ".graphifyprivate"
    original_private = private.read_text(encoding="utf-8")

    init_sharing(repo, interactive=False)

    assert shared.read_text(encoding="utf-8") == "custom/**\n", (
        "Second init_sharing must not overwrite user-edited .graphifyshared"
    )
    # Private overlay should also be preserved across reruns.
    assert private.read_text(encoding="utf-8") == original_private, (
        "Second init_sharing must not overwrite .graphifyprivate"
    )


def test_init_sharing_default_private_patterns_include_secrets(tmp_path: Path) -> None:
    """Default ``.graphifyprivate`` should include common secret/local patterns."""
    from graphify.migrate import init_sharing

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    init_sharing(repo, interactive=False)

    content = (repo / ".graphifyprivate").read_text(encoding="utf-8")
    expected = [".env", "secrets/", "_local."]
    missing = [pat for pat in expected if pat not in content]
    assert not missing, (
        f"Default .graphifyprivate is missing expected secret patterns: {missing}\n"
        f"Got contents:\n{content}"
    )


# ---------------------------------------------------------------------------
# suggest_classification_by_path
# ---------------------------------------------------------------------------


def test_suggest_classification_by_path_returns_correct_buckets(tmp_path: Path) -> None:
    """Path-based classifier must bucket ``src/`` and ``tests/`` as shared,
    ``scratch/`` as private."""
    from graphify.migrate import suggest_classification_by_path

    graph_path = tmp_path / "graph.json"
    _write_minimal_graph_json(
        graph_path,
        [
            {"id": "n1", "source_file": "src/foo.py", "label": "foo"},
            {"id": "n2", "source_file": "tests/test_foo.py", "label": "test_foo"},
            {"id": "n3", "source_file": "scratch/junk.py", "label": "junk"},
        ],
    )

    shared, private = suggest_classification_by_path(graph_path, interactive=False)

    # The function returns two lists of path-style strings.  We accept any
    # representation as long as src/tests live in shared and scratch in private.
    shared_blob = "\n".join(shared)
    private_blob = "\n".join(private)
    assert "src" in shared_blob, f"src/ must be classified as shared: {shared}"
    assert "tests" in shared_blob, f"tests/ must be classified as shared: {shared}"
    assert "scratch" in private_blob, f"scratch/ must be classified as private: {private}"


# ---------------------------------------------------------------------------
# migrate_to_shared
# ---------------------------------------------------------------------------


def test_migrate_to_shared_renames_legacy_json(tmp_path: Path) -> None:
    """``migrate_to_shared`` on a repo with a legacy graph.json must:

    * Create both overlay files.
    * Move ``graphify-out/graph.json`` to ``graphify-out/graph-private.json``.
    * Preserve ``built_at_commit`` and community labels in the renamed file.
    """
    from graphify.migrate import migrate_to_shared

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    graph_path = repo / "graphify-out" / "graph.json"
    _write_minimal_graph_json(
        graph_path,
        [{"id": "n1", "label": "n1", "community": 0}],
        built_at_commit="abc123",
        labels={"0": "Core Pipeline"},
    )

    migrate_to_shared(repo, interactive=False)

    assert (repo / ".graphifyshared").exists()
    assert (repo / ".graphifyprivate").exists()
    assert not graph_path.exists(), (
        "Legacy graph.json must be moved, not duplicated"
    )
    private_path = repo / "graphify-out" / "graph-private.json"
    assert private_path.exists(), "graph.json must be renamed to graph-private.json"

    data = json.loads(private_path.read_text(encoding="utf-8"))
    assert data.get("built_at_commit") == "abc123", (
        "Migration must preserve built_at_commit in graph-private.json"
    )
    assert data.get("community_labels") == {"0": "Core Pipeline"}, (
        "Migration must preserve community_labels in graph-private.json"
    )


def test_migrate_to_shared_idempotent(tmp_path: Path) -> None:
    """A second invocation must not re-migrate.  Spec ambiguity: the implementation
    may return ``already_initialized`` (overlays exist) or ``no_graph_json`` (the
    legacy file was renamed by the first run); in either case the second call must
    leave the first-run artefacts intact and avoid doing destructive work twice.
    """
    from graphify.migrate import migrate_to_shared

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _write_minimal_graph_json(
        repo / "graphify-out" / "graph.json",
        [{"id": "n1", "label": "n1"}],
    )

    first = migrate_to_shared(repo, interactive=False)
    assert first.get("status") == "migrated", (
        f"First migrate_to_shared must succeed, got {first!r}"
    )

    second = migrate_to_shared(repo, interactive=False)
    # Idempotency: do not return 'migrated' twice (would imply re-migration).
    assert second.get("status") != "migrated", (
        f"Second migrate_to_shared must NOT report 'migrated' again, got {second!r}"
    )
    assert second.get("status") in {"already_initialized", "no_graph_json"}, (
        f"Second migrate_to_shared returned unexpected status: {second!r}"
    )
    # First-run artefacts must remain untouched.
    private_path = repo / "graphify-out" / "graph-private.json"
    assert private_path.exists(), (
        "graph-private.json from first run must still exist after idempotent second run"
    )
    assert (repo / ".graphifyshared").exists()
    assert (repo / ".graphifyprivate").exists()


# ---------------------------------------------------------------------------
# add_pattern_to_overlay
# ---------------------------------------------------------------------------


def test_add_pattern_to_overlay_dedupes(tmp_path: Path) -> None:
    """Adding the same pattern twice must result in a single overlay line."""
    from graphify.migrate import add_pattern_to_overlay

    repo = tmp_path / "repo"
    repo.mkdir()
    # Per implementation signature, overlay is a literal "shared"|"private",
    # not a filename — the function maps to .graphifyshared / .graphifyprivate.
    (repo / ".graphifyshared").write_text("", encoding="utf-8")

    add_pattern_to_overlay(repo, "shared", ["src/**"], deduplicate=True)
    add_pattern_to_overlay(repo, "shared", ["src/**"], deduplicate=True)

    content = (repo / ".graphifyshared").read_text(encoding="utf-8")
    occurrences = [
        line for line in content.splitlines() if line.strip() == "src/**"
    ]
    assert len(occurrences) == 1, (
        f"Pattern 'src/**' must appear exactly once, got {len(occurrences)}\n"
        f"Content:\n{content}"
    )


def test_add_pattern_to_overlay_appends_new(tmp_path: Path) -> None:
    """A new pattern must be appended to an existing overlay (existing
    patterns preserved, new pattern at the end)."""
    from graphify.migrate import add_pattern_to_overlay

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".graphifyshared").write_text("A\nB\n", encoding="utf-8")

    add_pattern_to_overlay(repo, "shared", ["C"], deduplicate=True)

    lines = [
        line.strip()
        for line in (repo / ".graphifyshared").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines == ["A", "B", "C"], f"Expected ['A','B','C'], got {lines}"


# ---------------------------------------------------------------------------
# manifest_v1_to_v2
# ---------------------------------------------------------------------------


def _iter_file_entries(v2: dict):
    """Yield per-file entries from a v2 manifest.

    The implementation may store entries either at the top level (alongside
    schema_version) or nested under a 'files' key — accept both.
    """
    if isinstance(v2.get("files"), dict):
        yield from v2["files"].values()
        return
    for k, v in v2.items():
        if k == "schema_version":
            continue
        if isinstance(v, dict) and ("mtime" in v or "ast_hash" in v or "origin" in v):
            yield v


def test_manifest_v1_to_v2_stamps_private() -> None:
    """v1 manifest entries (no schema_version) must migrate to v2 with
    ``origin='private'`` stamped on every file entry."""
    from graphify.migrate import manifest_v1_to_v2

    v1 = {
        "src/foo.py": {"mtime": 1700000000.0, "ast_hash": "a", "semantic_hash": ""},
        "src/bar.py": {"mtime": 1700000001.0, "ast_hash": "b", "semantic_hash": ""},
    }
    v2 = manifest_v1_to_v2(v1)

    assert v2.get("schema_version") == 2, (
        f"schema_version must be 2 after migration, got {v2.get('schema_version')!r}"
    )

    entries = list(_iter_file_entries(v2))
    assert entries, f"v2 must contain per-file entries, got: {v2!r}"
    for entry in entries:
        assert entry.get("origin") == "private", (
            f"v1->v2 migration must stamp origin='private', got {entry!r}"
        )


def test_manifest_v1_to_v2_idempotent() -> None:
    """Re-running migration on a v2 manifest must be a no-op."""
    from graphify.migrate import manifest_v1_to_v2

    # Build a v2 manifest in both nested and flat shapes; the function should
    # cope with whichever shape it itself produces.  We probe with the nested
    # shape first, fall back to flat if the impl rejects it.
    nested_v2 = {
        "schema_version": 2,
        "files": {
            "src/foo.py": {
                "mtime": 1700000000.0,
                "ast_hash": "a",
                "semantic_hash": "",
                "origin": "shared",
            },
        },
    }
    out = manifest_v1_to_v2(dict(nested_v2))
    assert out.get("schema_version") == 2

    entries = list(_iter_file_entries(out))
    assert entries, f"v2 manifest must still have entries after re-migration: {out!r}"
    # The pre-stamped origin='shared' must not be downgraded to 'private'.
    origins = {e.get("origin") for e in entries}
    assert "shared" in origins, (
        f"Idempotent migration must preserve existing origin='shared', got origins={origins}"
    )


# ---------------------------------------------------------------------------
# D5 — init_sharing wires config.toml remote + merge-driver (Stage 2)
# ---------------------------------------------------------------------------


def test_init_sharing_writes_config_when_default_remote_given(tmp_path: Path, monkeypatch) -> None:
    """init_sharing with default_remote writes an entry to the config file."""
    import graphify.config as cfg
    from graphify.migrate import init_sharing

    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    init_sharing(repo, interactive=False, default_remote="ssh://example/foo.git")

    entries = cfg.load_config(cfg_file)
    assert len(entries) == 1, f"Expected 1 config entry, got {len(entries)}: {entries}"
    entry = entries[0]
    assert entry.url == "ssh://example/foo.git"
    assert entry.repo_path.resolve() == repo.resolve()


def test_init_sharing_does_not_write_config_when_no_default_remote(
    tmp_path: Path, monkeypatch
) -> None:
    """init_sharing without default_remote must not create the config file."""
    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    from graphify.migrate import init_sharing

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    init_sharing(repo, interactive=False)

    # Config file should not exist (or be empty / have no remote entries).
    import graphify.config as cfg
    entries = cfg.load_config(cfg_file)
    assert entries == [], f"Expected no config entries, got {entries}"


def test_init_sharing_installs_merge_driver_in_git_repo(tmp_path: Path, monkeypatch) -> None:
    """init_sharing inside a git repo registers the git merge driver."""
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git binary not available")

    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    from graphify.migrate import init_sharing
    import graphify.git_integration as gi

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    init_sharing(repo, interactive=False)

    assert gi.is_merge_driver_installed(repo) is True


def test_init_sharing_skips_merge_driver_outside_git_repo(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """init_sharing on a plain directory (no git) must not install merge driver
    and must not raise."""
    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    from graphify.migrate import init_sharing

    # Plain directory — no git init.
    repo = tmp_path / "plain_repo"
    repo.mkdir()

    init_sharing(repo, interactive=False)

    # .gitattributes must not have been created.
    assert not (repo / ".gitattributes").exists(), (
        ".gitattributes must not be created outside a git repo"
    )
    captured = capsys.readouterr()
    assert "installed git merge driver" not in captured.out


def test_init_sharing_merge_driver_install_is_idempotent(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Second call to init_sharing must not re-print the merge-driver message."""
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git binary not available")

    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    from graphify.migrate import init_sharing

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    # First call — merge driver not yet installed; should print.
    init_sharing(repo, interactive=False)
    first_out = capsys.readouterr().out
    assert "installed git merge driver" in first_out, (
        f"First init_sharing must print merge-driver message, got: {first_out!r}"
    )

    # Second call — already installed; must NOT print the message.
    init_sharing(repo, interactive=False)
    second_out = capsys.readouterr().out
    assert "installed git merge driver" not in second_out, (
        f"Second init_sharing must not re-print merge-driver message, got: {second_out!r}"
    )


def test_init_sharing_resilient_to_merge_driver_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """If install_merge_driver raises RuntimeError, init_sharing must not crash
    and must print the warning. Overlay files must still be created."""
    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    import graphify.git_integration as gi
    monkeypatch.setattr(gi, "install_merge_driver", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("test")))

    from graphify.migrate import init_sharing

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    # Must not raise.
    init_sharing(repo, interactive=False)

    captured = capsys.readouterr()
    assert "warning: could not install merge driver" in captured.out, (
        f"Expected warning in stdout, got: {captured.out!r}"
    )
    # Overlays must still be written.
    assert (repo / ".graphifyshared").exists(), ".graphifyshared must exist despite merge-driver error"
