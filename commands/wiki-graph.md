---
description: Build a knowledge graph of the workspace LLM Wiki from wikilinks
---

Build a static knowledge graph for `.wiki/` and write it to `.wiki/graph/`.

## Two-pass build

**Pass 1 — deterministic.** Walk every `.md` file under `.wiki/wiki/`. Extract every `[[Target]]` wikilink. Each page is a node; each wikilink is an edge with weight = occurrence count. Write `.wiki/graph/graph.json` with shape:

```json
{ "nodes": [{ "id": "PageName", "type": "source|entity|concept|synthesis", "tags": [] }],
  "edges": [{ "source": "A", "target": "B", "weight": 2, "kind": "explicit" }] }
```

**Pass 2 — semantic (optional).** For pages that share entities or tags but have no direct wikilink, infer implicit relationships and add edges with `kind: "inferred"` and a `confidence: 0.0–1.0`. Skip this pass if the wiki has fewer than ~10 pages.

## HTML rendering

Write `.wiki/graph/graph.html` as a self-contained vis.js page that loads `graph.json` from the same folder. No build step, no network dependency beyond the vis.js CDN. Color nodes by `type`, size by inbound edge count.

## Logging

Append `## [YYYY-MM-DD] graph | workspace` to `.wiki/log.md` with node and edge counts.
