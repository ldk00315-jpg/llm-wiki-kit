# merge 3 仕様 R2: 実物の Vault に当てて分かった2つの穴（仕様側の不備）

`docs/merge3-spec.md` の続き。現在の作業ツリー（A〜E 実装済み・222 passed / 44 skipped）の上に積む。
禁止事項・作法は同じ（Vault に触れない・commit / push しない・`tmp_path`）。

実物の共有 Vault（`I:\Workspace\.wiki`・**読むだけ**）に `validate --refs` を当てたら FAIL した。原因は実装ではなく仕様の穴。

## R2-1 【必須】後方互換: 既存の accepted event は `bound_to` を持たない

Vault には merge 3 より前に書かれた accepted event（`20260908T001351Z-c8e02b64`）がある。仕様 A の
「accepted は `bound_to` 必須」をそのまま適用すると、**過去に正しく書かれた event が今日から不正になる**。
event は append-only なので直せない。契約に反する（「自動失効なし」）。

修正:
- built-in と schema の両方で、decision(accepted) の `bound_to` を **optional** にする
- validate は「accepted に `bound_to` が無い」を **FAIL ではなく `legacy`（警告行）** として報告し、
  `bound_to` があるときだけ drift を検査する。要約に `legacy=N` を出す
- **新しい CLI（`decide accepted` / `rereview` from accepted）は必ず `bound_to` を書く**（ここは仕様 A のまま。
  hash 計算に失敗したら何も書かない）
- legacy を解消する正規の経路は **`rereview`**（人の明示指示で `bound_to` を付け直す）。契約と SKILL にそう書く
- 等価性テスト: `bound_to` 無しの accepted が **built-in でも schema でも通る**こと、CLI が書く accepted には必ずあること、
  validate が legacy を FAIL にしないこと。旧（R1）実装で赤になることを確認

## R2-2 【必須】frontmatter の入れ子を YAML ブロック形式で読む

仕様 A/C/D/E は proposal frontmatter の `source_refs` / `effect_contract` / `extensions` / `hosts` 等を読む前提だが、
kit の抽出器は flat `key: value` しか読めず、R1 は「入れ子は1行 JSON flow 形式」と規定した。
実物の proposal（`distill/monthly-listing-recon-csv/proposal.md`）は**普通のブロック形式 YAML** で書かれており、
人が書く proposal は今後もそうなる。1行 JSON を要求するのは非現実的で、C/D/E が実物で一度も効かない。

修正: **YAML の限定サブセットを読む total な parser を kit に置く**（built-in が正本、という作法どおり。
PyYAML があれば cross-check してよいが、判定の正本にはしない）。

対応する構文（これだけ・これ以上広げない）:
- ブロック mapping（`key: value`・インデントで入れ子）
- ブロック sequence（`- item`・item が scalar でも mapping でもよい。`- path: x` のように sequence 要素の先頭行が mapping 開始）
- scalar: プレーン／ダブルクォート（`\"` `\\` `\n` のエスケープ）／シングルクォート（`''`）
- flow 形式の `[a, b]` と `{k: v}`（1行のみ・入れ子なし）— 既存の `tags: [a, b]` 用
- `#` コメント（クォート外）・空行
- 型: クォート無しの `true/false/null/整数/小数` は型付き、それ以外は文字列。**`2026-09-05` のような日付は文字列のまま**
  （PyYAML が date 型にする挙動を再現しない。今回それで proposal schema が落ちた経緯がある）

対応しないもの（出てきたら **`unparseable`** として報告し、その proposal の refs は unverifiable にする。黙って部分的に読まない）:
- アンカー / エイリアス / タグ / 複数ドキュメント / `|` `>` のブロック scalar / 複数行 flow / タブインデント

- 関数名: `parse_frontmatter_yaml_subset(text) -> tuple[dict | None, list[str]]`（`(doc, problems)`。total）
- 既存の flat 抽出器は**置き換えない**（core の他の経路が使っている）。proposal を読むところだけ新 parser を使う
- テスト: 実物と同じ形の proposal frontmatter（source_refs 6件・effect_contract・hosts・extensions.revision・
  extracted_requirements・sensitive_inputs・review）を fixture にして、C の照合が **ok / mismatch / unverifiable を正しく出す**こと。
  対応しない構文ごとに `unparseable` になること。**R1 の「1行 JSON」fixture も引き続き通る**こと（JSON flow は YAML の部分集合）
- 契約 §10 の「1行 JSON flow 形式」の記述を、このサブセットの説明に置き換える

## R2-3 【小】`distill/_index.md` の不一致

実物で「`_index.md` が再生成結果と一致しません（reindex してください）」が出た。これは仕様どおりの検出で正しい
（`llmwiki.py reindex` は wiki 側の索引だけで、distill の index は `distill reindex`）。修正不要。SKILL.md の note / decide の
手順に「終わったら `distill reindex`」の1行があるか確認し、無ければ足す。

## 報告様式

R1 と同じ。加えて: R2-2 の parser が**対応しない構文を黙って読まない**ことをどう縛ったか。**commit・push はしない。**
