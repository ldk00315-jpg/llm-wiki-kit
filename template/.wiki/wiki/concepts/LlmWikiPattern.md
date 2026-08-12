---
title: LLM Wiki Pattern
summary: A local-first knowledge workflow where agents compile raw sources into a persistent, cross-linked Markdown wiki that compounds across sessions.
type: concept
tags: [knowledge-management, ai-agents, markdown, example]
sources: [../../raw/2026-08-05-llm-wiki-kit-design-notes.md]
created: 2026-08-05
updated: 2026-08-12
confidence: medium
---

# LLM Wiki Pattern

（このページはキット同梱のサンプルです。書き方の見本としてそのまま残しても、消して自分の最初のページを書いても構いません）

Andrej Karpathy が提唱したアイデアの実装: LLMエージェントに「自分専用のWiki」を持たせ、
セッションを跨いで知識を複利で積み上げる。

## 核心の設計

1. **Raw は不変・Synthesis は生きている** — 取り込んだ原文（raw/）は追記専用。
   エージェントが所有する統合ページ（wiki/）だけが更新され続ける。
2. **回答は Synthesis から** — 毎回原文を読み直すのではなく、統合済みのページ群から
   答える。だから知識が「複利」になる。
3. **呼び出しインデックス**（このキットの追加要素） — Wikiはpull型で、検索を
   思い付くかどうかが勘まかせになる弱点がある。SessionStartフックで
   「タイトル＋1行要約」の索引だけを毎セッション自動注入し、本文は必要時に
   開く2段構えにすると、道具が「感覚」に変わる。

## 使いどころ

- 苦労して見つけたデバッグの真因・外部制約の回避策
- 「なぜXでなくYにしたか」という判断の理由
- 再利用できるレシピ・コマンド・パターン

## 関連

- スキーマ（運用契約）: `.wiki/schema/AGENTS.llm-wiki.md`
