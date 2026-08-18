---
name: wiki-lint
description: Wikiの内容品質チェック（矛盾・孤立・陳腐化・出典の弱さ。LLMを使う）
---

# wiki-lint

`.wiki/` の**意味的な**品質を点検する。先に `wiki-health` を通し、構造検査が
通ってからここへ来る。この検査はLLMトークンを使う。

## 手順

1. **孤立ページ**: どの地図（syntheses）・索引からも参照されていないページ。
   統合か削除の候補として挙げる。
2. **壊れたリンク**: `[[wikilink]]` の指す先が存在しないもの。
   ただしコードブロック内の記法（TOMLの配列テーブル等）は対象外——
   **直さないと決めた項目は理由を添えて残す**。
3. **矛盾**: 同じ主題について食い違う記述。両方に出典を添えて報告する。
   片側だけ直さない。
4. **陳腐化**: 外部システム（他社API・サービス仕様）に依存するページで
   `updated` が古いもの。内部の原則やOSの罠は腐りにくいので区別する。
5. **要約の劣化**: `summary` が本文と乖離しているページ。索引に注入されるのは
   summary なので、ここが古いと「あるのに見つからない」状態になる。
6. **出典の弱さ**: `sources: []` のまま具体的な主張をしているページ、
   `confidence: high` なのに単一ソースのページ。
7. **信頼区分**: 外部由来なのに `trust` 未設定のページ。

## 出力

指摘ごとに「対象ページ・何が問題か・推奨する処置」。修正は自動で行わず、
ユーザーの承認を得てから着手する。

## メンテCLIの呼び出し

決定論的な操作はCore CLI（Python・依存ゼロ）が担う。索引・ログ・lockの
一貫性はCLI側で保証されるので、**同等の操作を手で書かない**こと。
ただしCore CLIに専用commandが無い本文等の操作はlock対象外であり、外部エディタと
同じ扱いになる。同じファイルを複数エージェントで同時編集せず、保存直前に
再読込して外部変更がないことを確認する。

```
python <kit>/core/llmwiki.py <command> --wiki-root <vault>
```

配備先では `scripts/llmwiki.py` に置かれていることが多い。Windowsでは
互換wrapper `scripts/llm-wiki.ps1` 経由でも同じ操作ができる
（`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\llm-wiki.ps1 <command> -WikiRoot <vault>`）。
