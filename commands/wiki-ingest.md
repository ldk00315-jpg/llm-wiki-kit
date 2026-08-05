---
description: Ingest a source into the workspace LLM Wiki and synthesize wiki pages
argument-hint: <path-or-url-or-inline-text> [--title "Title"]
---

Read `.wiki/schema/AGENTS.llm-wiki.md` first, then ingest `$ARGUMENTS` into the workspace LLM Wiki at `.wiki/`.

## Steps

1. **Save the raw source.** Run `pwsh -File scripts/llm-wiki.ps1 ingest -Source <path-or-url> -Title <title>` (or `-Text <inline>` for inline content). The helper writes the file into `.wiki/raw/YYYY-MM-DD-<slug>.md` with frontmatter.
2. **Read the saved raw file.** Identify durable concepts, named entities, and quotable claims.
3. **Create or update a source page** at `.wiki/wiki/sources/<slug>.md` with frontmatter `type: source` and a `sources:` list pointing back to the raw file. Include: one-paragraph summary, key claims (bulleted), notable quotes with line/section anchors, and `[[wikilinks]]` to entities and concepts touched.
4. **Create or update entity pages** at `.wiki/wiki/entities/<TitleCase>.md` (one file per named person, org, product). Each entity page accumulates claims across sources, each claim cited with a `[[wikilink]]` to the source page.
5. **Create or update concept pages** at `.wiki/wiki/concepts/<TitleCase>.md` for the durable ideas the source introduces or extends.
6. **Refresh `.wiki/wiki/overview.md`** so the Themes and Open Questions sections reflect the new source. Keep it tight — overview is a map, not a dump.
7. **Flag contradictions.** If a new claim conflicts with an existing wiki page, add a `> ⚠️ contradiction:` callout on both pages with citations to each source.
8. **Reindex.** Run `pwsh -File scripts/llm-wiki.ps1 reindex`.
9. **Append to the log.** The reindex script handles the reindex log entry; you must add an `## [YYYY-MM-DD] ingest | <title>` block to `.wiki/log.md` listing the new/updated wiki pages.

## Quality bar

- Never invent facts. Every claim on a synthesized page traces to a raw source.
- Use `[[PageName]]` wikilinks for cross-references. Pair with a normal Markdown link when the path differs from the title.
- Source slugs and source-page filenames: `kebab-case`. Entity and concept filenames: `TitleCase`.
- Frontmatter `confidence` reflects evidence strength: `low` (single weak source), `medium` (single solid source or multiple weak), `high` (multiple corroborating sources).
