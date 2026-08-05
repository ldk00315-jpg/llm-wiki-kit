---
description: Semantic content lint of the workspace LLM Wiki (uses LLM)
---

Run a semantic quality pass on `.wiki/`. Run `/wiki-health` first; only continue here if structural checks pass. This pass uses LLM tokens.

## Checks

1. **Orphan pages.** Synthesized pages with no inbound `[[wikilinks]]`. Suggest where to link them, or recommend deletion if redundant.
2. **Broken wikilinks.** `[[Targets]]` that point at non-existent pages. Suggest the closest existing page or propose creating the target.
3. **Contradictions across pages.** Compare claims that touch the same entity or concept. Flag with proposed reconciliations.
4. **Stale summaries.** Source pages whose `updated:` date is older than the latest source they cite.
5. **Missing entity / concept pages.** Names mentioned three or more times across sources without a dedicated page.
6. **Confidence drift.** Pages marked `high` confidence but only citing one source.
7. **Data gaps.** Open Questions in `overview.md` that no source addresses — propose what to ingest next.

## Output

A prioritized issue list with file paths and proposed fixes. Ask before applying judgment-heavy fixes (rewriting claims, merging pages). Apply mechanical fixes (broken-link redirects to obvious targets, frontmatter date refreshes) directly and log them.

After applying fixes, append `## [YYYY-MM-DD] lint | workspace` to `.wiki/log.md` with a summary of what changed.
