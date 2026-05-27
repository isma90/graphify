# Architecture

graphify is a Claude Code skill backed by a Python library. The skill orchestrates the library; the library can be used standalone.

## Pipeline

```
detect()  →  extract()  →  build_graph()  →  cluster()  →  analyze()  →  report()  →  export()
```

Each stage is a single function in its own module. They communicate through plain Python dicts and NetworkX graphs - no shared state, no side effects outside `graphify-out/`.

## Module responsibilities

| Module | Function | Input → Output |
|--------|----------|----------------|
| `detect.py` | `collect_files(root)` | directory → `[Path]` filtered list |
| `extract.py` | `extract(path)` | file path → `{nodes, edges}` dict |
| `build.py` | `build_graph(extractions)` | list of extraction dicts → `nx.Graph` |
| `cluster.py` | `cluster(G)` | graph → graph with `community` attr on each node |
| `analyze.py` | `analyze(G)` | graph → analysis dict (god nodes, surprises, questions) |
| `report.py` | `render_report(G, analysis)` | graph + analysis → GRAPH_REPORT.md string |
| `export.py` | `export(G, out_dir, ...)` | graph → Obsidian vault, graph.json, graph.html, graph.svg |
| `callflow_html.py` | `write_callflow_html(...)` | graphify-out files → Mermaid architecture/call-flow HTML |
| `ingest.py` | `ingest(url, ...)` | URL → file saved to corpus dir |
| `cache.py` | `check_semantic_cache / save_semantic_cache` | files → (cached, uncached) split |
| `security.py` | validation helpers | URL / path / label → validated or raises |
| `validate.py` | `validate_extraction(data)` | extraction dict → raises on schema errors |
| `serve.py` | `start_server(graph_path)` | graph file path → MCP stdio server |
| `watch.py` | `watch(root, flag_path)` | directory → writes flag file on change |
| `benchmark.py` | `run_benchmark(graph_path)` | graph file → corpus vs subgraph token comparison |

## Extraction output schema

Every extractor returns:

```json
{
  "nodes": [
    {"id": "unique_string", "label": "human name", "source_file": "path", "source_location": "L42"}
  ],
  "edges": [
    {"source": "id_a", "target": "id_b", "relation": "calls|imports|uses|...", "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}
  ]
}
```

`validate.py` enforces this schema before `build_graph()` consumes it.

## Split graph format

When the repo has `.graphifyshared` / `.graphifyprivate` overlays OR a configured remote in `~/.graphify/config.toml`, the pipeline produces **two** graph files instead of one:

- `graphify-out/graph-private.json` — local-only. Contains all nodes whose source file is NOT matched by `.graphifyshared`.
- `graphify-out/graph-shared.json` — synced via git. Contains nodes whose source file IS matched by `.graphifyshared`.

Both graphs follow the same node-link format as `graph.json`, with one additional attribute on every node AND every edge:

- `origin: "private" | "shared"` — which bucket this node/edge came from.

Cross-bucket edges (source in one bucket, target in the other) live in the **private** graph only. Hyperedges with any private member also live in private only. The shared graph never reveals which private files exist or how they connect to shared structure.

Repos without overlays AND without a configured remote stay in legacy mode and continue to produce a single `graph.json`, byte-identical to 0.8.18.

### Side files

| Path | Purpose | Tracked by git? |
|---|---|---|
| `graphify-out/graph-private.json` | Private bucket graph | NO (add to `.gitignore`) |
| `graphify-out/graph-shared.json` | Shared bucket graph | YES (committed; merge-driver-aware) |
| `graphify-out/.graphify_shared_base` | Last-pushed-or-pulled SHA; base for the next three-way merge | NO (add to `.gitignore`) |
| `graphify-out/.graphify_shared_conflicts.json` | List of `{id, base, ours, theirs}` from the last `graphify pull` | NO (add to `.gitignore`) |
| `.graphifyshared`, `.graphifyprivate` | Overlay patterns (gitignore-style) | YES (commit so teammates inherit) |
| `~/.graphify/config.toml` | Per-repo remote URL/branch config | n/a — lives in `$HOME`, never in the repo |

### Manifest v2

`graphify-out/.graphify_manifest.json` carries `schema_version: 2` and a per-file `origin` field when split mode is active. Lazy v1→v2 migration runs on load: v1 entries are stamped `origin: "private"` by default.

### Source code references

- `graphify/privacy.py` — `classify_paths`, `is_legacy_mode`, `load_overlays`. The classifier reuses `_parse_gitignore_line` and `_find_vcs_root` from `detect.py`.
- `graphify/extract.py:split_extraction_by_origin` — splits the merged extraction by bucket. `compute_bucket_migration_prune_ids` returns node IDs to prune from a bucket when a file moves to the other bucket between extracts.
- `graphify/build.py:three_way_merge_nodes(base, ours, theirs, *, max_nodes, dedup) -> (merged_graph, conflicts)` — the sync merge core, also delegated-to by the `graphify merge-driver` subcommand.
- `graphify/config.py` — TOML reader/writer for `~/.graphify/config.toml` (override via `GRAPHIFY_CONFIG`).
- `graphify/git_integration.py` — git plumbing wrappers (`install_merge_driver`, `commit_shared_via_plumbing` which never touches the working tree, `fetch_shared`, `push_branch`).
- `graphify/__main__.py:2344-2396` — `merge-driver` subcommand registered as `merge=graphify-shared` in `.gitattributes`.

## Confidence labels

| Label | Meaning |
|-------|---------|
| `EXTRACTED` | Relationship is explicitly stated in the source (e.g., an import statement, a direct call) |
| `INFERRED` | Relationship is a reasonable deduction (e.g., call-graph second pass, co-occurrence in context) |
| `AMBIGUOUS` | Relationship is uncertain; flagged for human review in GRAPH_REPORT.md |

## Adding a new language extractor

1. Add a `extract_<lang>(path: Path) -> dict` function in `extract.py` following the existing pattern (tree-sitter parse → walk nodes → collect `nodes` and `edges` → call-graph second pass for INFERRED `calls` edges).
2. Register the file suffix in `extract()` dispatch and `collect_files()`.
3. Add the suffix to `CODE_EXTENSIONS` in `detect.py` and `_WATCHED_EXTENSIONS` in `watch.py`.
4. Add the tree-sitter package to `pyproject.toml` dependencies.
5. Add a fixture file to `tests/fixtures/` and tests to `tests/test_languages.py`.

## Security

All external input passes through `graphify/security.py` before use:

- URLs → `validate_url()` (http/https only) + `_NoFileRedirectHandler` (blocks file:// redirects)
- Fetched content → `safe_fetch()` / `safe_fetch_text()` (size cap, timeout)
- Graph file paths → `validate_graph_path()` (must resolve inside `graphify-out/`)
- Node labels → `sanitize_label()` (strips control chars, caps 256 chars, HTML-escapes)

See `SECURITY.md` for the full threat model.

## Testing

One test file per module under `tests/`. Run with:

```bash
pytest tests/ -q
```

All tests are pure unit tests - no network calls, no file system side effects outside `tmp_path`.
