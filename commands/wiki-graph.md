---
description: Build a knowledge graph of the workspace LLM Wiki from wikilinks
---

Build a static knowledge graph for `.wiki/` and write it to `.wiki/graph/`.

## Two-pass build

**Pass 1 — deterministic.** Walk every `.md` file under `.wiki/wiki/`. Extract every `[[Target]]` wikilink. Each page is a node; each wikilink is an edge with weight = occurrence count. Node `id` is the vault-relative path (e.g. `wiki/concepts/Foo.md`) so same-titled pages never collide; keep the display title separately. Write `.wiki/graph/graph.json` with shape:

```json
{ "nodes": [{ "id": "wiki/concepts/Foo.md", "title": "Foo", "type": "source|entity|concept|synthesis", "tags": [] }],
  "edges": [{ "source": "wiki/concepts/A.md", "target": "wiki/concepts/B.md", "weight": 2, "kind": "explicit" }] }
```

**Pass 2 — semantic (optional).** For pages that share entities or tags but have no direct wikilink, infer implicit relationships and add edges with `kind: "inferred"`, a `confidence: 0.0–1.0`, and a `basis` field naming the shared entities/tags that justify the edge. Skip this pass if the wiki has fewer than ~10 pages.

## HTML rendering

Write `.wiki/graph/graph.html` as a **truly self-contained** page: embed the graph JSON inline in a `<script>` tag (do not `fetch()` a sibling file — `file://` blocks it) and render with inline JavaScript (plain canvas/SVG force layout is fine). **No CDN, no network dependency** — the page must open offline via `file://`. Color nodes by `type`, size by inbound edge count.

## Logging

Append `## [YYYY-MM-DD] graph | workspace` to `.wiki/log.md` with node and edge counts.
