---
name: wiki-health
description: Wikiの構造を決定論的に点検する軽量ヘルスチェック。日常確認や変更後の検証に使う
---

# wiki-health

`.wiki/` の構造的な健全性を決定論的に検査する。安価なので毎セッション回せる。
意味的な品質は `wiki-lint` が担当する。

## 手順

1. **Core CLI の `lint` を実行する**（下記「メンテCLIの呼び出し」）。
   決定論的な検査はすべてここに実装されている:
   必須ファイルの存在（`_index.md` / `raw/_index.md` / `wiki/_index.md` /
   `wiki/overview.md` / `schema/AGENTS.llm-wiki.md` / `log.md`）、
   frontmatterの開始と終端、`sources:` の有無、
   引用符なしの「コロン+空白」、方言外エスケープ、C0制御文字。
2. `.wiki/wiki/sources/` の各ページが、`sources:` で実在の raw を指しているか。
3. `.wiki/log.md` に各 source ページ相当の ingest 記録があるか。
4. 空スタブ（本文が概ね200文字未満の統合ページ）を一覧する。

## 出力

`error` / `warning` / `info` に分けたパンチリスト。すべて通れば
`OK: wiki is structurally healthy.` と出す。**ファイルを変更しない**。

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
