---
title: LLM Wiki Schema
summary: Operating contract for any agent reading or writing the workspace LLM Wiki at .wiki/. Includes the shared-Vault rules C-1…C-8 that keep multiple writers from diverging.
created: 2026-08-05
updated: 2026-08-19
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
    ├── sources/                  # optional source-level synthesis (see below)
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

### Source pages are optional

`wiki/sources/` is an **optional** layer. Create a source page only when the raw is
external, long, carries multiple distinct claims, or will be re-cited enough that a
stable per-source summary earns its keep. Short workspace notes do **not** get one —
concept and synthesis pages cite the raw directly.

**Raw count and source-page count are not expected to match.** `wiki-health` checks
that existing source pages point at real raws, and does **not** flag raws that have
no source page.

## Naming

- Raw files: `YYYY-MM-DD-<kebab-slug>.md`
- Source pages (`wiki/sources/`): `<kebab-slug>.md` — **optional layer**, see above
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
3. Create or update the relevant entity and concept pages. Create a **source page**
   only when the raw warrants one (see "Source pages are optional" above).
4. Refresh `wiki/overview.md`.
5. Reindex via the same CLI: `… llm-wiki.ps1 reindex`.
6. Append a `## [YYYY-MM-DD] ingest | <title>` entry to `log.md` listing the new/updated pages.

### Query

Driven by `/wiki-query`. Read `_index.md` first, then the smallest set of relevant wiki pages. Cite every non-trivial claim with `[[wikilinks]]`. If the wiki has no evidence, say so. If the answer is durable, offer to file it as `wiki/syntheses/<slug>.md`.

### Health

Driven by `/wiki-health`. Fast deterministic structural check: missing files, missing frontmatter, missing `sources:`, broken indexes, log gaps, empty stubs. Existing source pages must point at real raws; raws **without** a source page are not an error (the layer is optional). Safe to run every session.

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

If more than one agent writes this Vault, give new raw notes an `agent: <host>`
frontmatter field so provenance survives outside version control. Do not backfill
existing raws — that would be guessing.

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

## Shared-Vault operating rules (C-1 … C-8)

These apply whenever **more than one writer** touches this Vault — two agents, or an
agent plus a human with an editor. They are derived from a real incident, not theory.

> **The failure mode of a shared Vault is not collision — it is divergence.**
> On the first day of shared operation between two agents, the same finding was
> recorded twice, in two different pages. No lock contended. No `.conflict` file
> appeared. Every mechanism worked as designed, and the knowledge still split in two.
> Locks protect *simultaneous writes*; they do not protect against *the same subject
> being filed on two different shelves*. That is what C-1 … C-3 are for.

If only one writer ever touches this Vault, C-1 … C-5 cost you nothing to keep.

### C-1: Do not trust the index — verify against the live files before writing

The injected index is a **snapshot taken when your session started**. Pages another
writer added today are **not in it**. This is a structural property of passive
injection, not a bug.

Before creating a page, and before making a substantial addition to one:

1. Run the read-only candidate scan first (2026-08-31, Step 2), **both** commands:
   `python scripts/llmwiki.py resolve --title "<planned title>" --wiki-root <root>/.wiki`
   **and** `search --query "<subject words>"` — with rewordings of the subject,
   not just the planned title's own words. These are best-effort **lexical**
   resolvers: they rank candidates, they do not prove absence.
   `no-lexical-match` means "no similar spelling", not "no page about this" —
   a page under a different phrasing (paraphrase, EN⇄JA naming) will not be
   found by resolve alone; that is what the subject search with rewordings
   covers. On **any** candidate (including low-score ones listed under
   `no-lexical-match`), Read it before deciding: the machine only nominates,
   the merge-or-create judgement stays with the writer. If the CLI is
   unavailable, fall back to reading the live files across all of `wiki/` —
   filename, `title`, `summary` — not only `concepts/`: near-duplicates hide
   in `syntheses/` and `entities/`, and a page may exist under a different name.
2. Read the candidate page's own scope declaration (see C-3) before assuming it fits.
3. **Re-check immediately before writing**, not only when you started thinking about it.

Do **not** compare the index's page count against a directory's file count to decide
whether the index is stale — **the populations differ**. That comparison produces
false confidence.

Do **not** run a reindex just to make your own judgement easier: that writes a shared
file before the decision is made, and adds contention where there was none. The
read-only `resolve` / `search` commands scan the live files at query time and
never touch the index.

### C-2: Check fit before adding to an existing page

Before appending to an existing page, read its `title`, `summary`, and scope
declaration, and confirm your addition **belongs to that subject**. If it does not,
create a new page and link to it from the existing one with a single line.

*Origin: operational knowledge about a current toolkit was appended to a page about a
different, much older project — because the index made it look like the closest shelf.
The addition was correct; the shelf was not.*

### C-3: Declare what a page does and does not cover

Where several pages sit near the same subject, open each one with a one-line
declaration of what it covers and what it does not. This is what lets the next
reader — human or agent — pick the right shelf instead of the nearest one.

### C-4: Do not auto-commit a shared working tree

If this Vault is under version control and more than one writer touches it, an agent
running `git add` / `git commit` on its own will sweep up the other writer's
unfinished edits, and can collide on `.git/index.lock`.

- A Vault may name one **commit coordinator**. **If none is named, no agent commits.**
- Every non-coordinator writer **writes files and does not commit**.
- The coordinator never uses `git add -A`. It stages **explicit paths it has reviewed**,
  and does not mix two writers' changes into one commit.
- Commit messages name the writing agent, e.g. `wiki(<agent>): …`.

A writer that does not commit should leave a line in `inbox/journal.md` saying *why*
it changed something, so the coordinator can fold that reason into the commit message.
The journal already exists as a WAL; no new mechanism is needed.

### C-5: Recovery starts with reading, not with a destructive command

`git checkout -- <path>` **discards uncommitted work** and must not be the default move.

1. Look first: `git -C <vault> status`, `git diff`, `git log --oneline -- <path>`.
2. Identify the exact commit and path to restore.
3. **After a human approves**, use `git restore --source=<commit> -- <path>` or
   `git revert <commit>`, naming the path explicitly. Never restore the whole tree
   as a reflex.

Always pass `-C <vault>` so the operation cannot land on a different repository than
you intended.

### C-6: Writing outside the lock — optimistic concurrency

Core's `VaultLock` protects writes that go through the Core CLI (raw ingest, index,
log, journal). It does **not** protect a page body edited directly — that is treated
like an external editor. Version control gives after-the-fact recovery; it does
**not** prevent an overwrite.

When more than one agent (or a human with an editor) can write this Vault, editing a
page body outside Core requires:

1. **Re-read immediately before writing.** Never write from a copy read minutes ago.
2. **Apply a narrow patch that asserts its surrounding context**, not a whole-file
   overwrite. If the context no longer matches, the file changed underneath you.
3. **Create new pages exclusively.** If the path already exists, fail rather than
   overwrite — someone may have just created the page you are about to create.
4. **On mismatch, stop.** Re-read, merge deliberately, then write. Or leave a
   `.conflict` copy beside the file. Never overwrite silently.

Core exposes `atomic_write_text(..., expect_snapshot=...)` for exactly this check.

### C-7: Correcting a page — weaken the frontmatter with the body

**If you change or narrow a claim in the body, check the frontmatter in the same edit.**
`title` and `summary` are injected into every session through the passive index; the
body is read only by whoever opens the page. Correct the body alone and **the
un-corrected claim is the one that keeps circulating** — wider than the error you just
fixed. This is a context-distribution safety rule, not document tidiness.

### C-8: A WAL checkpoint preserves the originals losslessly

Deleting lines from `inbox/journal.md` is a **WAL checkpoint** — an irreversible
operation. Before any line is deleted, each deleted line must be persisted to `raw/`
**verbatim**. Organized, condensed, or thematic write-ups may coexist with the
originals; they may **not replace** them.

> **Duplication is recoverable. Loss is not.** When in doubt, keep both.

Completion conditions for a checkpoint — all of them:

1. Every line to be deleted exists in `raw/` verbatim.
2. That was **machine-verified** as an exact match (newline normalization only),
   preserving duplicate counts. Eyeballing cannot spot what a summary dropped, and
   word-coverage ratios are diagnostics, not proof of preservation.
3. The verification is deterministic and fails loudly on any missing line.
4. Only after the raw is atomically saved **and** verified are the verified lines
   deleted from the journal (atomic save). On partial failure, external modification,
   or any unclear state: leave the journal as is — **fail closed**.

What this rule does **not** forbid: a freshly written agent-authored workspace note is
itself a first-hand record and belongs in `raw/`. The rule forbids **replacing existing
input — journal lines, prior raws, external sources — with only a summary of it**.

*Origin (2026-08-19): a stocktake condensed 25 dense journal pointers into a thematic
summary and deleted the originals. The themes survived; the reusable specifics (URL
parameter formats, encodings, formulas, validation steps) did not. The summarizer
cannot see what it dropped: the missing details are, by definition, not on the page
being reread.*

## Safety rules

- Never invent facts to fill pages. Every claim on a synthesized page traces to a raw source.
- Raw files are append-only. Do not edit them after ingest except to fix mechanical metadata mistakes immediately.
- Keep `log.md` parseable: every entry shaped `## [YYYY-MM-DD] <action> | <subject>`.
- Prefer source traceability over polished prose.
- When a contradiction is detected, mark both sides — never silently overwrite.
