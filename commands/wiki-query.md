---
description: Answer a question from the workspace LLM Wiki, citing pages
argument-hint: <question>
---

Answer `$ARGUMENTS` using the workspace LLM Wiki at `.wiki/`.

## Steps

1. **Read `.wiki/_index.md` first** to scan available pages.
2. **Pick the smallest set of relevant pages** in `.wiki/wiki/` (sources, entities, concepts, syntheses, overview). Read those, and only their cited raw files in `.wiki/raw/` if a citation needs verification.
3. **Answer from the wiki.** Cite every non-trivial claim with a `[[PageName]]` wikilink. If the wiki has no evidence, say "the wiki has no evidence on this" — do not fabricate.
4. **Surface contradictions.** If two pages disagree, present both with citations.
5. **Offer to file the answer.** If the answer is durable and likely to be reused, ask whether to save it as `.wiki/wiki/syntheses/<kebab-case-slug>.md` with frontmatter `type: synthesis` and a `sources:` list of every page cited.
6. **Log the query.** Append a `## [YYYY-MM-DD] query | <short question>` entry to `.wiki/log.md` with the pages consulted.

## Style

- Lead with the answer. Citations come inline, not in a footer block.
- If the question is broad, scope it explicitly before answering.
