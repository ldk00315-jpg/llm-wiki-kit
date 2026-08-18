---
name: wiki-ingest
description: ソース（ファイル・URL・インラインテキスト）をWikiへ取り込み、統合ページを作る
---

# wiki-ingest

ソースをWikiへ取り込み、raw保存から統合ページの作成までを行う。

## 引数

`<path-or-url-or-inline-text>` — 取り込む対象。任意で `--title "タイトル"`。

## 手順

1. **運用契約を読む。** `.wiki/schema/AGENTS.llm-wiki.md` を先に読み、命名・
   frontmatter・記録基準を確認する。
2. **raw を保存する。** Core CLI の `ingest` を使う（下記「メンテCLIの呼び出し」）。
   `--source <path-or-url>` または `--text <inline>` に `--title <title>` を添える。
   `.wiki/raw/YYYY-MM-DD-<slug>.md` がfrontmatter付きで作られる。
3. **保存された raw を読む。** 再利用される概念・固有名・引用に値する主張を洗い出す。
4. **source ページを作る/更新する。** `.wiki/wiki/sources/<kebab-slug>.md`。
   frontmatter は `type: source`、`sources:` に raw への相対パス。
   本文は1段落の要約・要点の箇条書き・注目すべき引用・関連する
   `[[wikilinks]]`。
5. **entity ページを作る/更新する。** `.wiki/wiki/entities/<TitleCase>.md`
   （人物・組織・製品ごとに1ファイル）。各主張は source ページへの
   `[[wikilink]]` で出典を示す。
6. **concept ページを作る/更新する。** `.wiki/wiki/concepts/<TitleCase>.md`。
   そのソースが導入・拡張した「再利用される考え方」を書く。
7. **overview を更新する。** `.wiki/wiki/overview.md` の Themes と
   Open Questions に新しいソースを反映する。overview は地図であって
   ダンプではない——短く保つ。
8. **矛盾を明示する。** 既存ページと衝突する主張が出たら、**両方のページに**
   `> ⚠️ contradiction:` のcalloutを置き、双方の出典を引く。
9. **再索引する。** Core CLI の `reindex`。
10. **ログに追記する。** `.wiki/log.md` へ
    `## [YYYY-MM-DD] ingest | <title>` の見出しと、新規/更新したページの一覧。

## 品質基準

- **事実を作らない。** 統合ページのすべての主張は raw へ辿れること。
- 相互参照は `[[PageName]]`。パスがタイトルと違うときは同じ行にMarkdownリンクも
  併記し、Obsidian以外の読み手を切り捨てない。
- ファイル名: source は `kebab-case`、entity と concept は `TitleCase`。
- `confidence` は証拠の強さ: `low`（単一の弱いソース）/ `medium`（単一の
  確かなソース、または複数の弱いソース）/ `high`（複数の裏付け）。
- 外部由来で未確認の内容を取り込んだページには `trust: untrusted` を付ける。
  索引には要約が注入されなくなる（信頼境界）。

## メンテCLIの呼び出し

決定論的な操作はCore CLI（Python・依存ゼロ）が担う。索引・ログ・lockの
一貫性はCLI側で保証されるので、**同等の操作を手で書かない**こと。
ただしCore CLIに専用commandが無い統合ページ本文の生成はlock対象外であり、
外部エディタと同じ扱いになる。同じファイルを複数エージェントで同時編集せず、
保存直前に再読込して外部変更がないことを確認する。

```
python <kit>/core/llmwiki.py <command> --wiki-root <vault>
```

配備先では `scripts/llmwiki.py` に置かれていることが多い。Windowsでは
互換wrapper `scripts/llm-wiki.ps1` 経由でも同じ操作ができる
（`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\llm-wiki.ps1 <command> -WikiRoot <vault>`）。
