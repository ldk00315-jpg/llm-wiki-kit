---
description: Fast structural health check of the workspace LLM Wiki (no LLM synthesis)
---

Run a deterministic structural health check on `.wiki/`. This is the cheap, no-token check meant to run every session. For semantic quality issues use `/wiki-lint` instead.

## Steps

1. Run the structural lint and capture issues. Windows PowerShell 5.1:
   `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/llm-wiki.ps1 lint`
   (PowerShell 7+: `pwsh -File scripts/llm-wiki.ps1 lint`. If `scripts/llm-wiki.ps1`
   is not in this project, use the path where llm-wiki-kit is cloned.)
   The lint already checks: required files (`_index.md`, `raw/_index.md`,
   `wiki/_index.md`, `wiki/overview.md`, `schema/AGENTS.llm-wiki.md`, `log.md`),
   frontmatter presence, `sources:` presence, and raw-title YAML validity.
2. Verify every file in `.wiki/wiki/sources/` has a corresponding raw file referenced in its `sources:` frontmatter.
3. Verify `.wiki/log.md` has an ingest entry for every source page.
4. List empty stubs (synthesized pages whose body is shorter than ~200 chars).

## Output

Report a punch list, grouped by severity (`error`, `warning`, `info`). If everything passes, output `OK: wiki is structurally healthy.` Do not modify files.

---

> **移行のお知らせ**: このコマンドは `skills/wiki-health/SKILL.md` へ移行しました。
> 内容の正本はそちらで、CLIの呼び出しもPython Core（`core/llmwiki.py`）へ
> 更新されています。このファイルは既存利用者のための互換配置として残しています
> （削除の検討は将来のv2）。新規に導入する場合は Skills 側を使ってください。
