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

## Central knowledge groups

- A "group" (a client / platform / project) is a central knowledge base at `~/.graphify/<name>/` — its own git repo — that accumulates several repos into one graph. List them with `graphify project list`; see `docs/central-groups.md`.
- Create/grow a group: `graphify project create <name> --from <path>`, then `graphify project add <path> --group <name>` (accumulates; ungrouped repos keep using local `graphify-out/`).
- Query a group's graph with `--group`: `graphify query "<q>" --group <name>` (also `path` / `explain`).
- A repo's `graphify-out/.graphify_group` marker means it was imported into that group via `graphify project import`; the live graph lives in the group dir, not the repo.

## LLM backends (headless extraction)

- `graphify extract` picks a backend from the environment; force one with `--backend gemini|openai|claude|kimi|deepseek|ollama|bedrock|vertex|claude-cli`.
- `vertex` (Google Vertex AI) and `bedrock` (AWS) use cloud credentials, not API keys: `vertex` needs `GOOGLE_CLOUD_PROJECT` + ADC and is auto-detected only when `GOOGLE_GENAI_USE_VERTEXAI=true` (otherwise pass `--backend vertex`).
