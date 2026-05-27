---
name: graphify
description: "any input (code, docs, papers, images) → knowledge graph → clustered communities → HTML + JSON + audit report. Use when user asks any question about a codebase, project content, architecture, or file relationships — especially if graphify-out/ exists. Provides persistent graph with god nodes, community detection, and BFS/DFS query tools."
trigger: /graphify
---

# /graphify

Turn any folder of files into a navigable knowledge graph with community detection, an honest audit trail, and three outputs: interactive HTML, GraphRAG-ready JSON, and a plain-language GRAPH_REPORT.md.

## Usage

```
/graphify                     # full pipeline on current directory
/graphify <path>              # full pipeline on specific path
/graphify <path> --update     # incremental - re-extract only new/changed files
/graphify <path> --no-viz     # skip visualization, just report + JSON
/graphify <path> --wiki       # build agent-crawlable wiki
/graphify query "<question>"  # BFS traversal - broad context
graphify init-sharing [--default-remote <url>] [--non-interactive] [<root>]
                              # one-time team-sharing setup: writes .graphifyshared /
                              # .graphifyprivate overlays, records remote in
                              # ~/.graphify/config.toml, installs git merge driver
graphify push [--branch <name>] [--message <msg>] [<root>]
                              # publish local graph-shared.json to the configured remote
                              # (git plumbing — never touches your working tree)
graphify pull [--branch <name>] [<root>]
                              # fetch + three-way-merge the remote shared graph;
                              # conflicts written to graphify-out/.graphify_shared_conflicts.json
graphify remote {add|remove|list} ...
                              # manage the per-repo entry in ~/.graphify/config.toml
graphify install-merge-driver [<root>]
                              # register the git merge driver for graph-shared.json
graphify uninstall-merge-driver [<root>]
                              # remove it
```

## What You Must Do When Invoked

If the user invoked `/graphify --help` or `/graphify -h` (with no other arguments), print the contents of the `## Usage` section above verbatim and stop. Do not run any commands, do not detect files, do not default the path to `.`. Just print the Usage block and return.

**Fast path — existing graph:** Before doing anything else, check the filesystem for an existing graph in this order:

1. `graphify-out/graph-shared.json` and `graphify-out/graph-private.json` — **split mode**, the repo is team-sharing enabled. Load both; merge for in-memory queries so the user sees the union, but never propose writes that move nodes between them. The `origin` attribute on every node and every edge tells you which bucket it came from.
2. `graphify-out/graph.json` — **legacy mode**, single graph. Use as today.
3. Neither — run the full pipeline.

When you see split mode, you can suggest `graphify push` after the user makes changes they want to share, or `graphify pull` to fetch teammates' updates. Never invoke push/pull without an explicit user instruction or clear intent — they touch the git remote.

If no path was given, use `.` (current directory). Do not ask the user for a path.

Follow these steps in order. Do not skip steps.

**All commands use `python -c "..."` syntax — no bash heredocs, no shell redirects, no `&&`/`||`. This runs correctly on Windows PowerShell and macOS/Linux alike.**

### Step 1 - Ensure graphify is installed

```python
python -c "import graphify; import sys; from pathlib import Path; Path('graphify-out').mkdir(exist_ok=True); Path('graphify-out/.graphify_python').write_text(sys.executable)"
```

If the import fails, install first:

```python
python -m pip install graphifyy -q
```

Then re-run the Step 1 command.

### Step 2 - Detect files

```python
python -c "
import json, sys
from graphify.detect import detect
from pathlib import Path

result = detect(Path('INPUT_PATH'))
Path('graphify-out/.graphify_detect.json').write_text(json.dumps(result, indent=2))
total = result.get('total_files', 0)
words = result.get('total_words', 0)
print(f'Corpus: {total} files, ~{words} words')
for ftype, files in result.get('files', {}).items():
    if files:
        print(f'  {ftype}: {len(files)} files')
"
```

Replace `INPUT_PATH` with the actual path. Present a clean summary — do not dump the raw JSON.

- If `total_files` is 0: stop with "No supported files found in [path]."
- If `total_words` > 2,000,000 OR `total_files` > 200: warn the user and ask which subfolder to run on.
- Otherwise: proceed to Step 3.

### Step 3 - Extract entities and relationships

#### Part A - Structural extraction (AST, free, no API cost)

```python
python -c "
import json
from graphify.extract import collect_files, extract
from pathlib import Path

detect = json.loads(Path('graphify-out/.graphify_detect.json').read_text())
code_files = []
for f in detect.get('files', {}).get('code', []):
    p = Path(f)
    code_files.extend(collect_files(p) if p.is_dir() else [p])

if code_files:
    result = extract(code_files)
    Path('graphify-out/.graphify_ast.json').write_text(json.dumps(result, indent=2))
    print(f'AST: {len(result[\"nodes\"])} nodes, {len(result[\"edges\"])} edges')
else:
    Path('graphify-out/.graphify_ast.json').write_text(json.dumps({'nodes':[],'edges':[],'input_tokens':0,'output_tokens':0}))
    print('No code files - skipping AST extraction')
"
```

#### Part B - Semantic extraction (AI, costs tokens)

Skip if corpus is code-only (no docs, papers, or images).

Check cache first:

```python
python -c "
import json
from graphify.cache import check_semantic_cache
from pathlib import Path

detect = json.loads(Path('graphify-out/.graphify_detect.json').read_text())
all_files = [f for files in detect['files'].values() for f in files]
cached_nodes, cached_edges, cached_hyperedges, uncached = check_semantic_cache(all_files)

if cached_nodes or cached_edges:
    Path('graphify-out/.graphify_cached.json').write_text(json.dumps({'nodes': cached_nodes, 'edges': cached_edges, 'hyperedges': cached_hyperedges}))
Path('graphify-out/.graphify_uncached.txt').write_text('\n'.join(uncached))
print(f'Cache: {len(all_files)-len(uncached)} hit, {len(uncached)} need extraction')
"
```

For each chunk of uncached files (20-25 files per chunk), dispatch a subagent with this prompt:

```
You are a graphify extraction subagent. Read the files listed and extract a knowledge graph fragment.
Output ONLY valid JSON: {"nodes": [...], "edges": [...], "hyperedges": [...]}

Each node: {"id": "unique_id", "label": "Human Name", "file_type": "code|document|paper|image"}
Each edge: {"source": "id", "target": "id", "relation": "verb_phrase", "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}
hyperedges: [] unless you find a genuine group relationship

Files:
FILE_LIST
```

Collect all subagent responses and merge them:

```python
python -c "
import json
from pathlib import Path

# Merge: combine AST + cached + all semantic chunk results
all_nodes, all_edges, all_hyperedges = [], [], []

ast = json.loads(Path('graphify-out/.graphify_ast.json').read_text())
all_nodes.extend(ast.get('nodes', []))
all_edges.extend(ast.get('edges', []))

cached_path = Path('graphify-out/.graphify_cached.json')
if cached_path.exists():
    cached = json.loads(cached_path.read_text())
    all_nodes.extend(cached.get('nodes', []))
    all_edges.extend(cached.get('edges', []))
    all_hyperedges.extend(cached.get('hyperedges', []))

# PASTE each subagent response here as chunk_1, chunk_2, etc.
total_in, total_out = 0, 0
for chunk_json in []:  # replace [] with your chunk results
    chunk = json.loads(chunk_json) if isinstance(chunk_json, str) else chunk_json
    all_nodes.extend(chunk.get('nodes', []))
    all_edges.extend(chunk.get('edges', []))
    all_hyperedges.extend(chunk.get('hyperedges', []))
    total_in += chunk.get('input_tokens', 0)
    total_out += chunk.get('output_tokens', 0)

merged = {'nodes': all_nodes, 'edges': all_edges, 'hyperedges': all_hyperedges, 'input_tokens': total_in, 'output_tokens': total_out}
Path('graphify-out/.graphify_extract.json').write_text(json.dumps(merged, indent=2))
print(f'Merged: {len(all_nodes)} nodes, {len(all_edges)} edges')
"
```

### Step 4 - Build graph and cluster

```python
python -c "
import json
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.analyze import god_nodes, surprising_connections
from pathlib import Path

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text())
G = build_from_json(extraction)
communities = cluster(G)
gods = god_nodes(G)
surprises = surprising_connections(G, communities)

import networkx as nx
from networkx.readwrite import json_graph
graph_data = json_graph.node_link_data(G)
Path('graphify-out/graph.json').write_text(json.dumps(graph_data, indent=2))
Path('graphify-out/.graphify_analysis.json').write_text(json.dumps({
    'communities': {str(k): v for k, v in communities.items()},
    'cohesion': {},
    'god_nodes': gods,
    'surprises': surprises,
}, indent=2))
print(f'Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(communities)} communities')
print(f'God nodes: {[g[\"label\"] for g in gods[:5]]}')
"
```

### Step 5 - Generate report and visualization

```python
python -c "
import json
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.analyze import god_nodes, surprising_connections
from graphify.report import generate
from pathlib import Path

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text())
analysis = json.loads(Path('graphify-out/.graphify_analysis.json').read_text())

G = build_from_json(extraction)
communities = {int(k): v for k, v in analysis['communities'].items()}
gods = god_nodes(G)
surprises = surprising_connections(G, communities)

report = generate(G, communities, {}, {}, gods, surprises, extraction)
Path('graphify-out/GRAPH_REPORT.md').write_text(report)
print('GRAPH_REPORT.md written')
"
```

```python
python -c "
import json
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.export import to_html
from pathlib import Path

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text())
G = build_from_json(extraction)
communities = cluster(G)

try:
    to_html(G, communities, 'graphify-out/graph.html')
    print('graph.html written')
except ValueError as e:
    print(f'Visualization skipped: {e}')
"
```

### After completing all steps

Print this summary:

```
graphify complete
  graph.json      — GraphRAG-ready, queryable by MCP or CLI
  graph.html      — interactive visualization (open in browser)
  GRAPH_REPORT.md — plain-language architecture summary
```

Read `graphify-out/GRAPH_REPORT.md` and share the **God Nodes** and **Surprising Connections** sections directly in the chat — do not ask the user to open the file themselves.

---

## For team sharing (split mode)

Use when the repo has a private/shared split — `graph-private.json` stays local, `graph-shared.json` syncs to teammates via git.

### Mode detection

Detect mode from the filesystem before running graphify. The repo is in **split mode** if ANY of:

- Both `graphify-out/graph-shared.json` AND `graphify-out/graph-private.json` exist on disk.
- `.graphifyshared` or `.graphifyprivate` exists in the repo root or any ancestor up to the git root.
- A config entry exists for this repo in `~/.graphify/config.toml` (check via `graphify remote list | grep "$(pwd)"`).

Otherwise the repo is in **legacy mode** (single `graph.json`). Quick decision tree:

```bash
if [ -f graphify-out/graph-shared.json ] && [ -f graphify-out/graph-private.json ]; then
    : "split mode — load both"
elif [ -f graphify-out/graph.json ]; then
    : "legacy mode — load single graph"
else
    : "run /graphify to create one"
fi
```

### Mental model

`graph-private.json` stays on this machine and is never pushed. `graph-shared.json` syncs to teammates via the repo's git remote on a side branch (default `graphify/shared`). Files matched by `.graphifyshared` patterns produce shared nodes; everything else produces private nodes. Cross-bucket edges (and hyperedges with any private member) live in the private graph only — the shared graph never reveals which private files exist or how they connect.

### First-time setup

For a user wanting to enable sharing on a repo that currently produces a single `graph.json`:

```bash
# from the repo root
graphify init-sharing --default-remote git@github.com:org/repo.git .

# tell graphify which paths are shareable (gitignore-style patterns)
echo "src/**"  >> .graphifyshared
echo "docs/**" >> .graphifyshared

# commit the overlays so teammates inherit the classification
git add .graphifyshared .graphifyprivate .gitattributes
git commit -m "enable graphify team sharing"
```

`graphify init-sharing` writes the two overlay files, records the remote in `~/.graphify/config.toml`, and installs the git merge driver (only if `<root>` is inside a git repo). Then `graphify extract .` (or `/graphify`) produces both `graph-private.json` and `graph-shared.json`.

### Day-to-day

```bash
graphify extract .   # produces both files (incremental against existing ones)
graphify push        # publish your shared graph to the team
graphify pull        # fetch teammates' changes (three-way merged automatically)
```

`graphify push` uses git plumbing — it builds a commit object directly and pushes to the configured branch. It does NOT touch your working tree or `HEAD`, so it is safe to run mid-edit. After a successful push, `graphify-out/.graphify_shared_base` is updated to the new SHA.

`graphify pull` fetches the remote shared branch, runs a three-way merge using `.graphify_shared_base` as the base, and writes the merged result back to `graph-shared.json`. Any attribute conflicts the auto-merger could not resolve are written to `graphify-out/.graphify_shared_conflicts.json`.

### Conflict handling

When `graphify pull` writes `graphify-out/.graphify_shared_conflicts.json`, the file is a JSON list of unresolved conflicts:

```json
[
  {"id": "node-id", "base": {...}, "ours": {...}, "theirs": {...}},
  ...
]
```

Each entry shows the merge base attrs, the local (`ours`) attrs, and the remote (`theirs`) attrs. Surface this file to the user explicitly — do not silently move on. The user resolves by either editing `graph-shared.json` directly OR by re-extracting the affected source files locally and pushing again. After resolution, delete `graphify-out/.graphify_shared_conflicts.json` so the next pull starts clean.

### Side files

| Path | Purpose | Commit? |
|---|---|---|
| `graphify-out/graph-private.json` | Local-only graph for private files | NO |
| `graphify-out/graph-shared.json` | Synced graph for shared files | YES |
| `.graphifyshared`, `.graphifyprivate` | Overlay classification (gitignore-style patterns) | YES (commit so teammates inherit) |
| `graphify-out/.graphify_shared_base` | Sync watermark (last-pushed/pulled SHA) | NO (add to `.gitignore`) |
| `graphify-out/.graphify_shared_conflicts.json` | Last-pull's unresolved conflicts | NO (add to `.gitignore`) |
| `~/.graphify/config.toml` | Per-repo remote config | n/a — lives in `$HOME` |

### Rules (do / don't)

- Never propose moving a node between buckets without an explicit user instruction. The way to reclassify a file is to edit `.graphifyshared` or `.graphifyprivate` and re-run `graphify extract` — not to hand-edit either graph file.
- Never read `graph-private.json` from `git show` or any other git command — it is local-only and never committed; those queries will fail or read a stale artefact.
- Never invoke `graphify push` as a side-effect of any other action. Always confirm with the user before pushing — it touches the team's git remote.
- When the user asks "show me X" and X spans both buckets, query both files and report the union. Tell the user the result combines private + shared. Don't silently filter to one bucket.
- When `graphify-out/.graphify_shared_conflicts.json` exists, surface its contents before any further `graphify pull` — the file represents unresolved sync state that the next pull will overwrite.

### MCP server caveat

`python -m graphify.serve <path>` accepts any single graph file. In split mode, choose:

- `graphify-out/graph-shared.json` if the assistant only needs the shareable subgraph (e.g. when the LLM session might surface answers to teammates).
- `graphify-out/graph-private.json` for full-fidelity local queries that include private-only nodes and the cross-bucket edges.

The MCP server currently does not auto-detect split mode — the user must pass the desired graph path explicitly.

---

## For sleep cycle (Hermes integration)

Sprint 1 alpha ships Phases 1+2 (Replay + NREM consolidation). Full 5-phase cycle (SHY decay, REM creative, Wake briefing) lands in 0.10.0-alpha2 → rc1 → 0.10.0 stable.

### Mode detection

If `~/.hermes/state/graphify-sleep.manifest.json` exists in the user's HOME, the sleep cycle is installed. Inspect with:

```bash
graphify sleep status --json
```

The JSON output lists each scheduled phase with last-run timestamp, exit code, deltas, and cost. If `graphify sleep status` returns "not installed" or the manifest is absent, the cycle is dormant.

### When to suggest it

The user benefits from the sleep cycle when:
- They use Hermes as their primary agent
- They run `graphify` against a personal knowledge graph (`~/brain` or equivalent)
- Their `graph.json` is > 5000 nodes (smaller graphs don't need consolidation)
- They commit `graphify-out/graph.json` to git (the cycle relies on per-phase git commits)

Suggest it explicitly when the user asks "how do I keep my graph fresh overnight" or "how do I get morning briefings from my knowledge base."

### Mental model

- Hippocampus = `~/.hermes/MEMORY.md` (small, frozen per session, episodic)
- Neocortex = `graphify-out/graph.json` (large, persistent, semantic)
- The cycle moves consolidated knowledge from one to the other while the user sleeps.

### Setup (one-time)

```bash
graphify sleep install --brain-root ~/brain
# Paste the printed snippet into the user's Hermes chat session
```

The install command:
1. Copies the skill bundle to `~/.hermes/skills/sleep-cycle/` via `importlib.resources` + `shutil.copytree` (version-aware: re-installs over a different version do a clean rmtree+copy)
2. Writes the cron manifest to `~/.hermes/state/graphify-sleep.manifest.json` with Sprint 1's 2 jobs (Phases 1+2; more jobs added by future `graphify sleep install` after upgrades)
3. Prints a paste-into-chat snippet starting with `cronjob(action="delete", name="sleep_*_*")` lines (safe re-paste) followed by `cronjob(action="create", ...)` for each job
4. Reports: `[graphify sleep install] copied skill bundle to ~/.hermes/skills/sleep-cycle/ (version 0.10.0-alpha1)`

### Day-to-day

The cycle runs automatically each night at 02:00-05:00 UTC (configurable). No user action needed once installed. Each morning, MEMORY.md receives a new `Cortex YYYY-MM-DD HH:MM` entry (Sprint 4 ships this; Sprint 1 alpha-only stops at Phase 2 commit).

Common requests and their commands:

- "Show me last night's consolidation": `graphify sleep status --json`
- "Skip tonight": `graphify sleep pause --tonight`
- "Preview the cycle without waiting 24h": `graphify sleep demo --brain-root ~/brain`
- "Inspect a dream hypothesis": `graphify explain "H: <label>"` (Sprint 4+)
- "Remove the cycle entirely": `graphify sleep uninstall`

### Side files

| Path | Owner | Commit? |
|---|---|---|
| `graphify-out/graph.json` | the cycle (each phase commits) | YES |
| `graphify-out/.graphify_touched.json` | query-side touch log (Sprint 2 hook) | NO (`.gitignore`) |
| `~/brain/raw/day_<date>.md` | Phase 1 replay output | NO |
| `~/brain/raw/dreams/<date>.md` | Phase 4 hypothesize justification (Sprint 3) | NO |
| `~/.hermes/state/graphify-sleep.manifest.json` | install manifest | n/a |
| `~/.hermes/skills/sleep-cycle/` | distributed skill bundle | n/a |
| `~/.hermes/cron/output/<job_id>/cost.jsonl` | per-phase LLM cost tracking | n/a |

### Rules (do / don't)

- Never run `graphify push` (Stage 2) as a side-effect of the sleep cycle. The cycle operates on the local cortex; team sharing is a separate user-initiated flow.
- Never call `nx.contracted_nodes()` directly — fusion happens via `graphify fuse` (Sprint 3) which has hyperedge-rewrite safety baked in.
- When `.graphifyshared` or `.graphifyprivate` overlays exist (Stage 2 split mode), the sleep subcommands refuse to run with a clear error. Suggest the user either remove the overlays temporarily or use `--force-split-mode-unsafe` (Sprint 2+) which decays on the legacy graph only.
- Never invoke `cronjob(action="create", ...)` directly to register sleep cycle jobs. The user pastes the snippet from `graphify sleep install` because the snippet includes the delete+create pattern for safe re-registration.
- When the user reports "my MEMORY.md has multiple Cortex entries from today" — that's expected when the cycle ran more than once (e.g., user manually invoked `graphify sleep demo` then the cron also fired). Phase 5 (Wake, Sprint 4) cleans entries older than 7 days; same-day duplicates are intentional audit trail.
- Surface upstream failures explicitly. If `graphify sleep status` shows `[failed: ...] sleep_4_rem`, do not silently re-run the user's morning briefing — read the cron output and explain what failed.

### MCP server caveat

`python -m graphify.serve <path>` accepts any single graph file. In sleep cycle context, point it at `graphify-out/graph.json` (the cycle's working file). The MCP server doesn't yet detect Hypothesis nodes specially; use `graphify explain "H: ..."` for that.
