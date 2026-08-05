---
description: Fast structural health check of the workspace LLM Wiki (no LLM synthesis)
---

Run a deterministic structural health check on `.wiki/`. This is the cheap, no-token check meant to run every session. For semantic quality issues use `/wiki-lint` instead.

## Steps

1. Run `pwsh -File scripts/llm-wiki.ps1 lint` and capture issues.
2. Verify each subdirectory `_index.md` exists: `.wiki/_index.md`, `.wiki/raw/_index.md`, `.wiki/wiki/_index.md`, `.wiki/wiki/sources/_index.md`, `.wiki/wiki/entities/_index.md`, `.wiki/wiki/concepts/_index.md`, `.wiki/wiki/syntheses/_index.md`.
3. Verify every file in `.wiki/wiki/sources/` has a corresponding raw file referenced in its `sources:` frontmatter.
4. Verify `.wiki/log.md` has an ingest entry for every source page.
5. List empty stubs (synthesized pages whose body is shorter than ~200 chars).

## Output

Report a punch list, grouped by severity (`error`, `warning`, `info`). If everything passes, output `OK: wiki is structurally healthy.` Do not modify files.
