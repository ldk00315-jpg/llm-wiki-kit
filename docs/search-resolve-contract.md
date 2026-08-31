# search / resolve 契約: ライブカタログと重複確認

- 版: 1.0（**実装済み**）
- 日付: 2026-08-31
- 起案: なな（Claude / Anthropic）
- 位置づけ: 敵対的設計レビュー（2026-08-31）再設計②「受動注入の限定＋ライブカタログ」の
  検索側の確定契約。所見1（索引のページ飢餓）の恒久解の前半・所見2（C-1のO(N)人力検索）の解
- 実装: `core/llmwiki.py` の `collect_catalog` / `search_pages` / `resolve_title`

---

## 1. なぜこれが要るのか

Step 1で受動注入は「地図を優先＋最近の変更」に変わったが、予算から落ちたページへ
到達する手段が `wiki/_index.md` の目視しかなかった。また C-1（新設前の重複確認）は
全ページのtitle/summaryを人力走査させており、契約自身が二重登録事故を記録している。

本契約は両方を1つの検索基盤で解く:

- `search` — 落ちたページへの到達手段（注入ヘッダの「検索入口」行から案内）
- `resolve` — 新設前の重複判定（C-1の全表走査の機械化）

## 2. 設計判断: 派生DBを持たない「真のライブカタログ」

レビューの提案は「SQLite**等**のライブカタログ」。本実装は **クエリ時に実ファイルから
カタログを構築**し、DB・キャッシュ等の派生物を一切持たない。

理由:

1. 派生物は declared ≠ deployed のstaleness層（silent-failure型3）を丸ごと持ち込む。
   「常に新鮮」は staleness バグの類を構造的に消す
2. SQLite FTS5のCJKトークナイズ（trigram tokenizer）は SQLite ビルド依存で、
   依存ゼロ方針と環境差リスクが釣り合わない
3. 実測（2026-08-31・キャッシュ最適化後）: 実Vault 87ページで **search 31ms**。
   合成1,000ページで **search 328ms / resolve 310ms**（floorは1,000ファイルのread）

**切替条件**: ページ数の増加で1クエリが**1秒**を超えたら、本契約のCLIを不変のまま
backendだけを mtimeキャッシュ → SQLite FTS の順に差し替える。契約はインターフェイス
（§3〜§5）であり、実装方式ではない。

## 3. CLI

```
python llmwiki.py search  --query "<語 [語2 …]>" [--limit N] --wiki-root <root>
python llmwiki.py resolve --title "<新設したいタイトル案>" --wiki-root <root>
```

- PowerShell wrapper: `llm-wiki.ps1 search -Query "<語>" [-Limit N]` ／
  `llm-wiki.ps1 resolve -Title "<案>"`（値は環境変数 `LLMWIKI_QUERY` / `LLMWIKI_LIMIT` /
  `LLMWIKI_TITLE` 経由——PS 5.1の引用符問題の回避・従来と同じ方式）
- 終了コード: 0=正常（該当なしも正常）／1=引数不足
- 副作用なし（読み取りのみ・lockを取らない）

## 4. search の契約

### 4-1. 照合対象と重み

frontmatterの **title / ファイル名（stem）/ tags / summary / sources** の5フィールド。
本文（body）は照合しない（注入抑制の迂回路にしない・重複判定に不要）。

空白区切りの各語について、正規化済み部分一致でフィールド重み
（title 100 / stem 60 / tags 50 / summary 30 / sources 20）を加点し、
クエリ全体とタイトルのbigram近似（≥0.2）も加点する。
**重みの具体値は契約ではない**（順位性質のみ契約: 後述のテスト固定分）。

### 4-2. 正規化

NFKC → 小文字 → 空白・約物の除去。日本語は分かち書きせず文字単位の
部分一致＋文字bigramで照合する（全半角・大小・記号ゆれをここで吸収）。

### 4-3. trust / F-06 との整合（重要）

titleとsummaryは索引と同じ `_index_entry_fields` を通る。したがって
`trust: untrusted` および F-06 WARN該当ページのsummaryは
**表示にも照合にも使われない**——検索を注入抑制の迂回路にしない。
出力の全文字列は `sanitize_injection_text` 済み。

### 4-4. 決定性

同点は updated降順 → rel昇順。同一Vault・同一クエリの結果は常に同一。

### 4-5. 出力

```
[LLM Wiki検索] query="…" 該当N件中 M件を表示（全Pページ走査）:
- <title> — <summary> [<rel>]
```

該当なしは「該当なし＋未記録の可能性が高い」を明示する
（「見つからない＝存在しない」ではないことを読み手に伝える）。

## 5. resolve の契約

### 5-1. 照合

候補タイトルを、既存ページの **title・基底title（末尾括弧書き補足を除去）・stem** と
比較し、文字bigram Dice係数と包含率（len短/len長）の大きい方を類似度とする。
基底title比較は「長い括弧書き補足がbigramを希釈して実質同題を見逃す」実測
（0.46→0.81）への対処。

### 5-2. 判定

| 最大類似度 | 判定 | 案内 |
|---|---|---|
| ≥ 0.5 | `duplicate-likely` | 新設せず既存ページへの追記・更新を第一候補に |
| ≥ 0.3 | `similar` | Readで本文確認→統合可否を判断してから作成 |
| 未満 | `none` | 新規作成してよい（**C-1の全表走査はこの結果で代替できる**） |

閾値はテストで固定（変更にはテスト更新＝契約変更を伴う）。

### 5-3. C-1 との関係

resolveは C-1 の「書く前に実ファイルで確認」の**走査部分**を機械化する。
`none` 以外が出たら、従来どおり該当ページをReadして人間/LLMが最終判断する
（機械判定は候補提示まで。統合するかの意味判断は書き手の責務のまま）。
TOCTOU（確認〜作成の間の並行作成）はresolveでは解けない——Step 3の課題。

## 6. 注入ヘッダの検索入口（再設計②の完成）

`build_index_context` のヘッダに検索入口1行が入る
（`core-api-contract.md` §3-1b）。これで受動注入は
**固定MOC（地図優先）＋最近の変更（updated降順）＋検索入口** の3要素になり、
再設計②の受動注入側の形が完成する。

## 7. テストによる契約固定

`tests/test_search.py`（14件）。fixtureはR-05の教訓に従い、素朴な誤実装
（ファイル名順・recency順・常にnone/常にlikely）では失敗するよう逆向きに配置:

- 順位性質: title照合 > summary照合／全語ヒット > 部分ヒット（更新日・名前順と逆配置）
- tags/sources照合・日本語部分一致・決定性・limit
- 抑制summaryの非照合・非表示（4-3）
- resolve閾値の両側（duplicate-likely / similar / none / 完全一致=1.0）と括弧書き剥離
- 検索入口行の存在と予算不変条件の維持
- CLI終了コード
