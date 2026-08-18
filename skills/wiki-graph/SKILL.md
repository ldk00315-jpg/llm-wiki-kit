---
name: wiki-graph
description: wikilinkからナレッジグラフを生成し、オフラインで開けるHTMLに書き出す
---

# wiki-graph

`.wiki/` の wikilink からナレッジグラフを構築し、`.wiki/graph/` へ書き出す。

## 2段の構築

**Pass 1 — 決定論的。** `.wiki/wiki/` 配下の全 `.md` を走査し、`[[Target]]` を
すべて抽出する。ページがノード、wikilinkがエッジ（重み＝出現回数）。
ノードの `id` は **Vault相対パス**（例 `wiki/concepts/Foo.md`）にする——
同名ページが衝突しないため。表示名は別フィールドに持つ。

```json
{ "nodes": [{ "id": "wiki/concepts/Foo.md", "title": "Foo", "type": "source|entity|concept|synthesis", "tags": [] }],
  "edges": [{ "source": "wiki/concepts/A.md", "target": "wiki/concepts/B.md", "weight": 2, "kind": "explicit" }] }
```

**Pass 2 — 意味的（任意）。** 直接のリンクは無いが同じ固有名やタグを共有する
ページ間に、`kind: "inferred"`・`confidence: 0.0–1.0`・**`basis`（根拠となった
共有要素）** を持つエッジを足す。ページ数が10未満なら省く。

## HTML出力

`.wiki/graph/graph.html` を**真に自己完結**な1ファイルとして書く。

- グラフJSONは `<script>` にインラインで埋め込む（`fetch()` は `file://` で
  ブロックされるため使わない）
- 描画も同ファイル内のJavaScriptで行う（素のcanvas/SVGで十分）
- **CDN・外部ネットワーク依存を持たない**——オフラインで開けること
- ノードは `type` で色分け、大きさは被リンク数

## ログ

`.wiki/log.md` へ `## [YYYY-MM-DD] graph | workspace` とノード数・エッジ数。

## メンテCLIの呼び出し

決定論的な操作はCore CLI（Python・依存ゼロ）が担う。索引・ログ・lockの
一貫性はCLI側で保証されるので、**同等の操作を手で書かない**こと。

```
python <kit>/core/llmwiki.py <command> --wiki-root <vault>
```

配備先では `scripts/llmwiki.py` に置かれていることが多い。Windowsでは
互換wrapper `scripts/llm-wiki.ps1` 経由でも同じ操作ができる
（`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\llm-wiki.ps1 <command> -WikiRoot <vault>`）。
