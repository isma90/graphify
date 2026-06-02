# Central knowledge groups (`graphify project`)

By default graphify writes its graph next to the code it scanned
(`<repo>/graphify-out/`). That couples the graph to one folder and names it
after whatever directory you happened to run in.

A **knowledge group** (a "project") instead stores the graph centrally at
`~/.graphify/<name>/`, under a **semantic name you choose** — a client, a
platform, or a whole project that spans several repos. graphify manages the
contents of that folder exactly as it manages `graphify-out/` today, and the
group **accumulates knowledge incrementally**: point it at another repo/dir
later and that knowledge is merged into the same growing graph.

Each `~/.graphify/<name>/` is **its own git repo**, so history, backups, and the
[sleep cycle](sleep-cycle.md) all work there; you can optionally push it to a
dedicated remote.

## Mental model

| | |
|---|---|
| **name** | a client / project / platform — whatever you want to group |
| **`~/.graphify/<name>/`** | a git repo holding `graphify-out/graph.json` (one growing graph) |
| **sources** | the repos/dirs you add; each contributes nodes into the single group graph |

This is distinct from `graphify global` / `graphify merge-graphs`, which keep
each repo's nodes in a **separate namespace** (prefixed ids). A group **unifies**
knowledge into one graph.

## Commands

```bash
# Create a group (its own git repo at ~/.graphify/agrosuper) and seed it.
graphify project create agrosuper --from ~/repos/agrosuper/db-corpus

# Accumulate more sources into the SAME growing graph.
graphify project add ~/repos/agrosuper/api --group agrosuper
graphify project add ~/repos/agrosuper/web --group agrosuper

# Inspect.
graphify project list --verbose

# Query the group's graph.
graphify query "how does the api talk to the db" --group agrosuper
graphify explain "LoginService" --group agrosuper
graphify path "api" "database" --group agrosuper

# Share (optional): set a dedicated remote, then push.
graphify project remote agrosuper git@github.com:me/agrosuper-graph.git
graphify project push agrosuper
```

`graphify extract <path> --group <name>` is the low-level primitive;
`graphify project add` is the ergonomic wrapper that also records the source.
`--group` is mutually exclusive with `--out`.

## Push, remotes, and error handling

- `graphify project remote <name> <url>` provides the remote URL (required for
  push) and registers it as `origin` in the group repo.
- `graphify project push <name>` commits pending artifacts, then pushes.
  - **No remote configured** → the graph is still committed **locally** and you
    get a message telling you to set a remote. Nothing is lost.
  - **Push fails** (network/auth/rejection) → git's stderr is shown to you and
    the local commit stays intact; rerunning `graphify project push <name>` is
    safe.

## Migrating an existing `graphify-out/`

```bash
# Move an existing repo-local graph into a new central group.
graphify project import ~/repos/agrosuper/db-corpus --group agrosuper

# Or fold it into an existing group (accumulates instead of moving).
graphify project import ~/repos/agrosuper/api --group ignored --merge-into agrosuper
```

`import` is idempotent: it leaves a `.graphify_group` marker in the repo's
`graphify-out/` and refuses to re-import.

## Backwards compatibility

Repos that are **not** assigned to a group behave exactly as before — a local
`graphify-out/graph.json`, byte-for-byte. The central-group flow is fully
opt-in.

## Current limitations (v1)

- **Single graph per group.** Split mode (`graph-private.json` /
  `graph-shared.json`) is ignored inside a group — mirroring how the sleep
  cycle already refuses split mode. Deferred.
- **No cross-source pruning.** Re-adding a source merges/grows but does not
  prune files that were deleted from that source. The content-hash cache keeps
  re-adds cheap.
- **Multi-collaborator three-way merge** of a shared central graph (Stage-2
  style) is deferred; `push` to a dedicated remote is supported, `pull`/merge
  is not yet.
