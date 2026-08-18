---
name: wiki-query
description: Wikiから質問に答える。出典をページ単位で引き、証拠が無ければ無いと言う
---

# wiki-query

Wikiの知識だけで質問に答え、出典を明示する。

## 引数

`<question>` — 答えたい質問。

## 手順

1. **索引を先に読む。** `.wiki/wiki/_index.md`（全カテゴリのカタログ）と
   `.wiki/wiki/overview.md`。ページを総なめしない。
2. **最小の関連ページ集合を選ぶ。** `.wiki/wiki/` 配下から読み、引用の裏取りが
   必要なときだけ `.wiki/raw/` の該当ファイルを開く。
3. **Wikiから答える。** 自明でない主張はすべて `[[PageName]]` で出典を示す。
   **Wikiに証拠が無ければ「Wikiにはこの件の記録がない」と言う**——作らない。
4. **矛盾を表に出す。** 2つのページが食い違うなら、両方を出典付きで併記する。
5. **答えの保存を提案する。** 再利用されそうな答えなら、
   `.wiki/wiki/syntheses/<kebab-slug>.md` として保存するか尋ねる。
   frontmatter は `type: synthesis`、`sources:` に引用した全ページ。
6. **ログに追記する。** `.wiki/log.md` へ
   `## [YYYY-MM-DD] query | <短い質問>` と参照したページ。

## スタイル

- 結論から書く。出典は本文中にインラインで置き、末尾にまとめない。
- 質問が広すぎるときは、答える前に範囲を明示して絞る。

## メンテCLIの呼び出し

決定論的な操作はCore CLI（Python・依存ゼロ）が担う。索引・ログ・lockの
一貫性はCLI側で保証されるので、**同等の操作を手で書かない**こと。

```
python <kit>/core/llmwiki.py <command> --wiki-root <vault>
```

配備先では `scripts/llmwiki.py` に置かれていることが多い。Windowsでは
互換wrapper `scripts/llm-wiki.ps1` 経由でも同じ操作ができる
（`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\llm-wiki.ps1 <command> -WikiRoot <vault>`）。
