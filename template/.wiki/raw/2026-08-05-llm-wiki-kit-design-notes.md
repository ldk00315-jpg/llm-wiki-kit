---
title: "llm-wiki-kit design notes (bundled sample source)"
source: "llm-wiki-kit (bundled with the kit itself)"
kind: text
ingested: 2026-08-05
status: raw
---

# llm-wiki-kit design notes (bundled sample source)

（このrawファイルはキット同梱のサンプルソースです。同梱サンプルページ
`wiki/concepts/LlmWikiPattern.md` の `sources:` が指す実体で、
「すべてのclaimはraw sourceへ追跡可能」というスキーマ規則の見本を兼ねます）

- LLM Wiki の原アイデアは Andrej Karpathy の提案: エージェントに自分専用の
  Markdown wiki を持たせ、raw（不変の原文）と synthesis（LLM所有の統合）を
  分離して、セッションを跨いで知識を複利で積み上げる。
- このキットの追加要素は「呼び出しインデックス」: 全文でなくタイトル＋1行要約を
  SessionStart で注入する二段階想起。pull型（検索を思い付くかが勘まかせ）の
  弱点を、受動注入で構造的に潰す。
- 運用契約（スキーマ）に「何を記録し、何を記録しないか」「memory との境界」
  「コンパクション前に何を守るか」を明文化し、エージェントが日常作業の中で
  自発的に wiki を育てられるようにする。
