---
description: Rewrite the wiki Overview page from current sources, entities, and concepts
---

Rewrite `.wiki/wiki/overview.md` so it reflects the current state of the wiki. The Overview is the page a reader lands on cold; it should answer "what does this wiki know?" in under one screen.

## Inputs

Read `.wiki/wiki/_index.md`（全カテゴリのカタログ）and the Open Questions section of the existing `overview.md`.

## Output sections

1. **Themes** — 3–7 bullets, each a recurring theme across sources. Each theme bullet ends with `[[wikilinks]]` to the 1–3 most relevant pages.
2. **Key entities** — top 5 entities by inbound link count.
3. **Key concepts** — top 5 concepts by inbound link count.
4. **Open Questions** — preserve existing questions unless a new source has answered them; add new questions surfaced by recent ingests.
5. **Recent Activity** — last 5 entries from `.wiki/log.md`.

Refresh the `updated:` frontmatter date. Append `## [YYYY-MM-DD] overview | workspace` to `.wiki/log.md`.

---

> **移行のお知らせ**: このコマンドは `skills/wiki-overview/SKILL.md` へ移行しました。
> 内容の正本はそちらで、CLIの呼び出しもPython Core（`core/llmwiki.py`）へ
> 更新されています。このファイルは既存利用者のための互換配置として残しています
> （削除の検討は将来のv2）。新規に導入する場合は Skills 側を使ってください。
