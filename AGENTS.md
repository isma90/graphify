## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)

## Team sharing (split mode)

- If `graphify-out/graph-shared.json` and `graphify-out/graph-private.json` exist, the repo is in split mode. Use both for queries; never copy nodes between buckets without editing `.graphifyshared` / `.graphifyprivate` and re-running `graphify extract`.
- Never commit `graphify-out/.graphify_shared_base` or `graphify-out/.graphify_shared_conflicts.json`. They are local-only sync state.
- After modifying code in a split-mode repo, run `graphify extract` (incremental — fast). Only run `graphify push` when the user explicitly asks to share.
- If `graphify-out/.graphify_shared_conflicts.json` exists, the last `graphify pull` left unresolved conflicts. Surface its contents to the user before running further sync.
- Per-repo remote URLs live in `~/.graphify/config.toml`. To inspect what's configured, run `graphify remote list`.
