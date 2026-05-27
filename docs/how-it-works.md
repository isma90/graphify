# How graphify works

## The three passes

graphify processes your files in three passes:

**Pass 1 — Code structure (free, no API calls)**
Tree-sitter parses your code files and extracts classes, functions, imports, call graphs, and inline comments. This runs locally with no LLM involved. 25 languages supported. SQL files get special treatment: tables, views, foreign keys, and JOIN relationships are extracted deterministically.

Code files are not sent to the LLM semantic extractor in the normal pipeline. If a corpus contains only code files, Pass 3 is skipped entirely; semantic extraction is reserved for docs, papers, images, and transcripts.

**Pass 2 — Video and audio (local, no API calls)**
Video and audio files are transcribed with faster-whisper. To focus the transcript on your domain, the transcription prompt is seeded with your top god nodes (the most-connected concepts in your code graph so far). Transcripts are cached — re-runs skip already-processed files.

**Pass 3 — Docs, papers, images (Claude subagents, costs tokens)**
Claude runs in parallel over markdown, PDFs, images, and transcripts. Each subagent reads a batch of files and outputs a JSON fragment: nodes, edges, and any group relationships. The fragments are merged into a single graph.

Before Pass 3, optional converters turn supported pointer/binary formats into
Markdown sidecars under `graphify-out/converted/`. Office files (`.docx`,
`.xlsx`) use the `[office]` extra. Google Workspace shortcuts (`.gdoc`,
`.gsheet`, `.gslides`) are opt-in with `--google-workspace` or
`GRAPHIFY_GOOGLE_WORKSPACE=1` and require an authenticated `gws` CLI.

---

## How community detection works

Communities are found using the [Leiden algorithm](https://www.nature.com/articles/s41598-019-41695-z) — a graph-clustering method that groups nodes by edge density. Nodes with many connections between them end up in the same community.

**No embeddings needed.** The semantic similarity edges that Claude extracts (`semantically_similar_to`) are already in the graph, so they influence community shape directly. The graph structure is the similarity signal — there's no separate embedding step or vector database.

---

## Confidence tagging

Every relationship is tagged with one of three labels:

| Tag | Meaning |
|-----|---------|
| `EXTRACTED` | Found directly in the source (e.g. a function call, an import) |
| `INFERRED` | A reasonable inference Claude made, with a `confidence_score` (0.0–1.0) |
| `AMBIGUOUS` | Uncertain — flagged in the report for manual review |

EXTRACTED edges always have confidence 1.0. INFERRED edges use a discrete rubric:
- **0.95** — near-certain (explicit cross-file reference, one plausible target)
- **0.85** — strong evidence (naming + context align)
- **0.75** — reasonable (contextual but not explicit)
- **0.65** — weak (naming similarity only)
- **0.55** — speculative

---

## Token benchmark

The first run extracts and builds the graph — this costs tokens. Every subsequent query reads the compact graph instead of raw files. That's where the savings compound.

On a mixed corpus (Karpathy repos + 5 papers + 4 images, 52 files): **71.5x fewer tokens per query** vs reading the raw files directly.

| Corpus | Files | Reduction |
|--------|-------|-----------|
| Karpathy repos + papers + images | 52 | **71.5x** |
| graphify source + Transformer paper | 4 | **5.4x** |
| httpx (synthetic Python library) | 6 | ~1x |

Token reduction scales with corpus size. Six files already fits in a context window — the graph value there is structural clarity, not compression. At 52 files the savings compound quickly.

Each `worked/` folder in the repo has the raw input files and actual output (`GRAPH_REPORT.md`, `graph.json`) so you can run it yourself and verify.

---

## Parallel extraction

Code files are extracted in parallel using `ProcessPoolExecutor` — bypasses Python's GIL for genuine multiprocessing. Doc/paper/image batches are dispatched as parallel Claude subagents. On a corpus of 84 code files, parallel AST extraction runs in about 1.66x less time than sequential.

---

## SHA256 cache

Every extracted file is fingerprinted by content hash. Re-runs skip unchanged files entirely — only new or modified files go through extraction again. The cache lives in `graphify-out/cache/`.

---

## The graph format

The output `graph.json` uses NetworkX's node-link format. Each node has:
- `id` — stable identifier
- `label` — human-readable name
- `file_type` — `code`, `document`, `paper`, `image`, `rationale`
- `source_file` — where it came from

Each edge has:
- `source`, `target` — node IDs
- `relation` — verb phrase (e.g. `calls`, `imports`, `implements`, `semantically_similar_to`)
- `confidence` — `EXTRACTED`, `INFERRED`, or `AMBIGUOUS`
- `confidence_score` — float (INFERRED only)
- `source_file` — where the relationship was found

Hyperedges (group relationships connecting 3+ nodes) live in `G.graph["hyperedges"]`.

---

## Team sync (split mode)

### Why two graphs

Think of `.graphifyshared` as `.gitignore` for the knowledge graph. Some files in a repo are meant for the whole team — production code, public docs, design notes. Others are not — scratch experiments, half-finished refactors, vendor secrets, your personal TODO file. graphify already respects `.graphifyignore` for "don't index this at all," but split mode adds a second axis: "index it, but keep it local."

When `.graphifyshared` (or a configured remote in `~/.graphify/config.toml`) is present, graphify produces two files instead of one. `graph-shared.json` is what teammates pull; `graph-private.json` is what you query locally. The shared graph is constructed so it never leaks which private files exist — no `source_file` references into private paths, no community membership that would imply a hidden node, no edges that point at something a teammate cannot see.

### The trust boundary

The split is not just a filter applied at export time; it is a hard partition that flows through the whole pipeline. Cross-bucket edges (an import from a public file into a private helper, a `calls` edge from a shared function to a private utility) live in the **private** graph only. Hyperedges that include any private member do the same. This means `graph-shared.json` is a STRICT subgraph of the full union — never a teaser, never an alias, never a placeholder.

The trade-off is honest: queries run against `graph-shared.json` alone will sometimes show shared nodes as "isolated" when, in your local view, they actually have private neighbours. That asymmetry is the price of the privacy guarantee. Use `graph-private.json` locally to see the full picture; share `graph-shared.json` confident that it cannot betray what's on your machine.

### How sync works

`graphify push` writes the local `graph-shared.json` to a side branch (default `graphify/shared`) on the same git remote that hosts the corpus. It uses git's low-level plumbing (`update-ref`, `hash-object`, `commit-tree`) so it never touches your working tree — no checkout, no merge, no stray file. The SHA it just published is recorded in `graphify-out/.graphify_shared_base`, which becomes the common ancestor for the next merge.

`graphify pull` fetches the same side branch, reads the SHA in `.graphify_shared_base` as the base, and runs `three_way_merge_nodes(base, ours, theirs)`. Conflicts — nodes where you and a teammate both edited the same attribute to different values — are RECORDED, not failed-on. They go into `graphify-out/.graphify_shared_conflicts.json` as a list of `{id, base, ours, theirs}` entries for you to resolve at your own pace. The merge itself always succeeds; the file you read next is a valid graph.

There is one more sync path to know about. When you and a teammate both edit `graph-shared.json` and both `git pull` the corpus, git itself encounters a merge. `init-sharing` installs a merge driver in `.git/config` that registers `graph-shared.json` as `merge=graphify-shared` in `.gitattributes`. When git triggers the driver, it shells out to `graphify merge-driver`, which delegates to the same `three_way_merge_nodes`. The net effect: concurrent edits to the shared graph union cleanly, without you ever seeing conflict markers in JSON.

### ASCII flow

```text
   ┌─────────────────────────────────────────────────────────────┐
   │  graphify init-sharing --default-remote <url> .             │
   │  echo "src/**" >> .graphifyshared                           │
   │  echo "docs/**" >> .graphifyshared                          │
   └────────────────┬────────────────────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  graphify extract  →  graph-private.json + graph-shared.json│
   └────────────────┬────────────────────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  graphify push    →  commits graph-shared.json to side branch│
   └────────────────┬────────────────────────────────────────────┘
                    │  (teammate's session)
                    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  graphify pull    →  three-way merge; conflicts to JSON file │
   └─────────────────────────────────────────────────────────────┘
```
