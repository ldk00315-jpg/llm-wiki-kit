---
title: LLM Wiki Schema
summary: Operating contract for any agent reading or writing the workspace LLM Wiki at .wiki/.
created: 2026-08-05
updated: 2026-08-10
---

# LLM Wiki Schema

Read this file before operating on the workspace `.wiki/` directory. It encodes the canonical Karpathy LLM Wiki pattern adapted for day-to-day agent work.

## Purpose

Maintain a compounding Markdown knowledge base owned by the agent. Raw sources are immutable input. Synthesized wiki pages are LLM-owned, cross-linked, and kept current. Knowledge compounds: queries draw from synthesis, not from re-deriving raw sources every time.

## Directory contract

```
.wiki/
├── _index.md                     # master navigation + stats
├── log.md                        # append-only operation log
├── raw/                          # immutable source captures
│   └── _index.md
├── inbox/                        # temporary drop zone for un-ingested files
│   └── journal.md                # one-line write-ahead log for maybe-wiki-grade moments
├── assets/                       # local images / attachments
├── schema/
│   └── AGENTS.llm-wiki.md        # this file
└── wiki/                         # LLM-owned synthesis
    ├── _index.md                 # catalog of every wiki page
    ├── overview.md               # living single-page synthesis (entry point)
    ├── sources/                  # one summary page per ingested raw source
    ├── entities/                 # one page per named person/org/product
    ├── concepts/                 # one page per durable idea/framework
    └── syntheses/                # query answers filed as durable pages
```

## Page format

Every synthesized page in `wiki/` starts with YAML frontmatter:

```yaml
---
title: Human readable title
summary: One sentence summary.
type: source | entity | concept | synthesis
tags: [tag-one, tag-two]
sources: [../../raw/2026-08-05-example.md]
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: low | medium | high
---
```

`sources:` is a list of paths or `[[wikilinks]]`. `confidence` reflects evidence strength (`low` = single weak source, `high` = multiple corroborating sources).

## Naming

- Raw files: `YYYY-MM-DD-<kebab-slug>.md`
- Source pages (`wiki/sources/`): `<kebab-slug>.md`
- Entity pages (`wiki/entities/`): `<TitleCase>.md`
- Concept pages (`wiki/concepts/`): `<TitleCase>.md`
- Synthesis pages (`wiki/syntheses/`): `<kebab-slug>.md`

## Cross-references

Use `[[PageName]]` wikilinks for graph navigation. When the link target's path differs from the title, pair the wikilink with a Markdown link on the same line so non-Obsidian readers can follow it.

## Workflows

### Ingest

Driven by `/wiki-ingest` (see the kit's `commands/wiki-ingest.md`). High-level:

1. Save the raw source via the maintenance CLI (`llm-wiki.ps1 ingest …` — on Windows PowerShell 5.1 run it as `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/llm-wiki.ps1 ingest …`; with PowerShell 7 use `pwsh -File …`).
2. Read it; extract durable concepts, entities, claims, contradictions, open questions.
3. Create or update a source page, plus relevant entity and concept pages.
4. Refresh `wiki/overview.md`.
5. Reindex via the same CLI: `… llm-wiki.ps1 reindex`.
6. Append a `## [YYYY-MM-DD] ingest | <title>` entry to `log.md` listing the new/updated pages.

### Query

Driven by `/wiki-query`. Read `_index.md` first, then the smallest set of relevant wiki pages. Cite every non-trivial claim with `[[wikilinks]]`. If the wiki has no evidence, say so. If the answer is durable, offer to file it as `wiki/syntheses/<slug>.md`.

### Health

Driven by `/wiki-health`. Fast structural check (no LLM tokens): missing files, missing frontmatter, missing `sources:`, broken indexes, log gaps, empty stubs. Safe to run every session.

### Lint

Driven by `/wiki-lint`. Semantic content quality (uses LLM tokens): orphans, broken wikilinks, contradictions, stale summaries, missing entity/concept pages, confidence drift, data gaps. Run after health passes.

### Graph

Driven by `/wiki-graph`. Two-pass build: explicit wikilinks then optional inferred relationships. Output `.wiki/graph/graph.json` and `graph.html`.

### Overview

Driven by `/wiki-overview`. Rewrite `wiki/overview.md` from the current state of sources, entities, concepts.

## Auto-capture during everyday work

Beyond explicit `/wiki-ingest` calls, the agent records durable work content into the wiki as it happens. This is the day-to-day flow that keeps the wiki growing without ceremony.

### Capture when

Work produces something the user would want to find again:

- A decision with non-obvious reasoning ("why we picked X over Y").
- A debug finding that took effort to land ("the cause was Z, not the obvious A").
- An architectural choice or pattern adopted in the workspace.
- A workaround for an external constraint (a flaky API, an OS quirk, a tool limitation).
- An open question that surfaced and isn't yet answered.
- A reusable snippet, command, or recipe that worked on the first try after research.

### Do not capture

- Trivial mechanical edits or code that documents itself.
- User preferences and collaboration facts — those belong in the agent's memory system, not the wiki.
- Ephemeral session state, in-progress work, or anything the next conversation will rederive easily.
- Anything the user explicitly said not to keep.

### Mechanism (no new commands)

1. Write a raw note directly to `.wiki/raw/YYYY-MM-DD-<kebab-slug>.md` with the raw frontmatter (`title`, `source: workspace`, `kind: workspace-note`, `ingested: YYYY-MM-DD`, `status: raw`). Body is short — bullet points or a paragraph.
2. Update or create the relevant page in `wiki/concepts/`, `wiki/syntheses/`, or `wiki/entities/`. Cite the new raw file in `sources:` and link to related pages with `[[wikilinks]]`.
3. Append `## [YYYY-MM-DD] capture | <subject>` to `.wiki/log.md`.
4. If a synthesized page was created or an entity/concept was added, run the maintenance CLI's `reindex` (see the Ingest section for the invocation form). If only an existing page was edited in place, skip the reindex.

Keep it light. One small raw file plus one wiki-page touch is the typical capture. If a raw note is growing long, the signal probably belongs in the synthesized page instead.

### Compaction boundary rule — never let wiki-grade knowledge cross a compaction

Long sessions get compacted (summarized). Compaction keeps *what was done* and drops *why, the details, and the failure path* — exactly the meat of wiki-grade knowledge. The moment knowledge is freshest (a bloated context) is also the moment just before it is lost. Therefore:

1. **Write it now.** Once something qualifies under "Capture when", record it in the same stretch of work — do not defer to end-of-session, which a compaction may never let arrive.
2. **One-line journal as WAL.** When unsure whether something is wiki-grade, or when mid-task with no room for a full capture, append one line to `.wiki/inbox/journal.md`:
   `- [YYYY-MM-DD] <one-line pointer to the insight and where it happened>`
   This is a write-ahead log: the pointer survives outside the context window even if the session is compacted before the full page is written. Full page creation happens at the next natural pause.
3. **A bloated context is the stocktake signal.** Context size is a proxy for accumulated session knowledge. When compaction is near (or has just happened), review `.wiki/inbox/journal.md`, promote entries that qualify into pages, and delete the promoted lines. The journal should trend toward empty.
4. **Environment-side backstop.** Wire the kit's `hooks/precompact_hook.py` (PreCompact) and `hooks/wiki_index_hook.py` (SessionStart) — see the kit README. The former appends a durable boundary marker to `inbox/journal.md` just before compaction; the latter injects a recovery instruction when the session resumes with `source: "compact"`. Both are safety nets, not substitutes for rules 1-3.

### Wiki vs. memory boundary

- **Wiki** holds *work content* — decisions about this codebase, findings about this system, patterns adopted in this workspace, reusable recipes.
- **Memory** holds *collaboration context* — how the user prefers to work, current project state, references to external systems.

When in doubt: if a future agent reading the workspace cold would benefit, it's wiki. If a future agent collaborating with the user would benefit, it's memory.

## Safety rules

- Never invent facts to fill pages. Every claim on a synthesized page traces to a raw source.
- Raw files are append-only. Do not edit them after ingest except to fix mechanical metadata mistakes immediately.
- Keep `log.md` parseable: every entry shaped `## [YYYY-MM-DD] <action> | <subject>`.
- Prefer source traceability over polished prose.
- When a contradiction is detected, mark both sides — never silently overwrite.
