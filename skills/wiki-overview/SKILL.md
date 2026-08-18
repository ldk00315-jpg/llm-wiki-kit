---
name: wiki-overview
description: 入口ページ overview.md を現在のWikiの状態から書き直す
---

# wiki-overview

`.wiki/wiki/overview.md` を、いまのWikiの状態を映すように書き直す。
overview は**コールドで来た読み手が最初に着地するページ**。
「このWikiは何を知っているか」に1画面で答えること。

## 入力

`.wiki/wiki/_index.md`（全カテゴリのカタログ）と、既存 `overview.md` の
Open Questions 節。ページ数が多い場合は、まず syntheses（地図ページ）を見る。

## 出力する節

1. **地図（MOC）への分岐** — ドメインごとの地図ページがあれば、それを表にして
   「こんなときはこれを開く」を示す。個別ページの総なめを不要にするのが目的。
2. **Themes** — 3〜7個。ソース横断で繰り返し現れる主題。各項目の末尾に
   最も関連する1〜3ページへの `[[wikilinks]]`。
3. **横断する最重要ページ** — 複数ドメインに効く原則ページがあれば強調する。
4. **Open Questions** — 既存の問いは、新しいソースが答えていない限り残す。
   最近の取り込みで浮上した問いを足す。
5. **運用** — 記録ルールの所在（schema）とログの場所。

## 仕上げ

frontmatter の `updated` を更新し、`.wiki/log.md` へ
`## [YYYY-MM-DD] overview | workspace` を追記する。

**入口が古くなると入口でなくなる。** ページが増えたら書き直す。

## メンテCLIの呼び出し

決定論的な操作はCore CLI（Python・依存ゼロ）が担う。索引・ログ・lockの
一貫性はCLI側で保証されるので、**同等の操作を手で書かない**こと。
ただしCore CLIに専用commandが無いoverview本文の生成はlock対象外であり、
外部エディタと同じ扱いになる。同じファイルを複数エージェントで同時編集せず、
保存直前に再読込して外部変更がないことを確認する。

```
python <kit>/core/llmwiki.py <command> --wiki-root <vault>
```

配備先では `scripts/llmwiki.py` に置かれていることが多い。Windowsでは
互換wrapper `scripts/llm-wiki.ps1` 経由でも同じ操作ができる
（`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\llm-wiki.ps1 <command> -WikiRoot <vault>`）。
