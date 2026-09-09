# merge 3 仕様: ゲートの hash 束縛・再レビューの再束縛・refs 照合・schema の小物

対象: `core/distill.py`・`schema/distill/*.json`・`docs/distillation-contract.md`・`tests/test_distill_cli.py`・`tests/test_distill_schemas.py`。
**先に読むもの**: `docs/distillation-contract.md`（状態機械と人間ゲート）、`core/distill.py` 全体（特に `TRANSITIONS`・
`builtin_validate_event`・`validate_event`・`cmd_decide`・`cmd_nominate`・`cmd_validate`・`state_chain`）、既存テスト。
テスト: `I:\Workspace\eBay\OpenLister\.venv\Scripts\python.exe -m pytest tests/ -q`（現状 181 passed / 33 skipped。消さない）。

## 0. 原則（この kit の作法・merge 2 で固まったもの）

- **検証の正本は built-in**（`builtin_validate_event`）。schema は cross-check。両者の**等価性テスト**を必ず更新する
- 検証器は任意の JSON 値に対して total（例外を出さず問題を返す）。**型を確かめてから membership**
- 触る前に確かめる：mutating verb は lock 内で `assert_store_healthy` を通してから書く。**書く前に壊れうる計算を全部済ませる**
- event は 1 file 1 event・exclusive create・append-only。`previous_event_id` の連鎖で順序を決める
- 「コマンドの結果」と「対象の状態」を stdout で混ぜない
- 主張を実装より強くしない。保証しないことは docstring と契約に書く
- **commit / push はしない**。作業は `I:\Workspace\llm-wiki-kit` の中だけ。`I:\Workspace\.wiki`（共有 Vault）には触れない——テストは `tmp_path` に store を作る

背景: 候補(a)（`monthly-listing-recon-csv`）を実際にゲートへ通したとき、次の穴を手作業で埋めた。それを kit に戻す。

## A. `decide accepted` が proposal / effect contract / candidate bundle の hash を自動で束縛する

契約 §6 は「accepted は proposal hash と effect-contract hash に束縛する」と言うが、CLI は page_sha256 しか束縛しない。
今回は reason 文に hash を手書きした。

- event schema の `decision` に **`bound_to`**（object・optional）を追加:
  `{"proposal": {"path": <portable_path>, "sha256": <sha>}, "effect_contract": {...}, "candidate_bundle": {"sha256": <sha>} }`
  `proposal` / `effect_contract` は `new_state == accepted` のとき**必須**、`candidate_bundle` は optional
- `cmd_decide(... accepted)` は `distill/<skill_slug>/proposal.md` と `effect-contract.json` を **1回だけ読んだ bytes** から hash を計算して
  `bound_to` に入れる。`skill_slug` は proposal frontmatter の `skill_slug` から取る（frontmatter は既存の抽出器を使う。独自 parser を増やさない）。
  どちらかが無い／読めない／frontmatter が壊れている → **DistillError で何も書かない**（accepted を hash なしで出せる経路を残さない）
- `--bundle-sha256 <sha>` を optional で受け、64桁 hex でなければ引数エラー
- `held` / `rejected` では `bound_to` を付けない（付いていたら validate が拒む）
- **validate**: accepted の head を持つ candidate について、`bound_to.proposal.sha256` / `effect_contract.sha256` が**現在の実体**と一致しなければ
  「proposal drift」「effect-contract drift」を報告して FAIL（page drift と同じ扱い。自動失効なし・fail-closed）
- built-in 検証: `bound_to` の型・必須・sha256 形式・portable_path を total に検査

## B. 人の再レビュー後に page identity を束縛し直す 1 イベント

承認済み／nominated のページを直すと page drift で fail-closed になる。今回は `decide held → nominate` の2発で代用したが、
人がやったのは1回のレビューなのに記録が2つになる。

- 新 event_type **`rereviewed`**（source: human・strength: observed）。`TRANSITIONS` に追加:
  `from: ("nominated", "accepted")` → `to: 同じ state`（state は変えない。subject の `page_sha256` を**現在の実体**で束縛し直す）
- `accepted` から `rereviewed` するときは、A の `bound_to` も**再計算して付け直す**（proposal / effect が変わっていればその hash も更新。
  つまり「accepted のまま、承認対象を今の内容へ更新した」の意味。人の明示指示でしか出ない）
- CLI: `distill rereview <distill_id> --reason "..."`。lock 内・health gate 後・page が read できなければ何も書かない
- validate の「page drift」判定は、head の**page_sha256 を持つ最新 state event**（`nominated` / `decision` / `rereviewed`）を基準にする。
  `state_chain` に `rereviewed` を組み込む（順序は `previous_event_id`）
- schema の `event_type` enum と built-in の `EVENT_TYPES` / `TRANSITIONS` / 必須・禁止フィールド表を揃え、等価性テストで縛る

## C. `validate --refs`：source_refs と effect_contract の実体 hash 照合

Codex が手で見つけた R-01（proposal の正本ページ hash が実体と不一致）は機械で拾えたはず。

- `distill validate --refs` で、各 candidate の `distill/<slug>/proposal.md` frontmatter の
  `source_refs[].{path,sha256}` と `effect_contract.{path,sha256}` を実体と照合する
- **path の解決**は暗黙にしない:
  - `wiki/…` / `distill/…` で始まる path → Vault root 配下（既存の `resolve_under_base` で containment）
  - それ以外 → 先頭セグメントを **base id** とみなし、`--ref-base <id>=<abs_dir>`（複数可）で与えられた base 配下に解決
    （例: `eBay/OpenLister/src/x.py` → `--ref-base eBay=I:/Workspace/eBay`）。base 配下の containment を字句＋実解決で確認
  - base が与えられていない・containment 違反 → その ref は **`unverifiable`** として報告（FAIL にしない。「検証していない」を「一致」と混ぜない）
- 結果は 3 値で数える: `ok` / `mismatch`（→ FAIL）/ `unverifiable`（→ 報告のみ）。stdout の要約に3つの件数
- `source_refs` の `role: runtime` と `wiki-page` 等の区別は**報告に載せる**だけ（Codex との合意: source ref は再レビュー trigger、
  runtime ref は実行前 fail-close。ここは trigger 側なので mismatch でも「再レビューが要る」の意味）

## D. proposal schema に `document_revision` / `supersedes` を常設

`proposal_version` は schema 版の const。文書の改訂番号を置く場所が無く `extensions.revision` で逃がしている。

- top-level に optional の `document_revision`（string・`^\d+\.\d+$`）と `supersedes`（string）を追加
- `extensions.revision` は引き続き許容（後方互換）。両方あるときは top-level を正とし、validate が食い違いを報告

## E. effect contract に `expected_accounts` を昇格

候補(a) で invariant が固まった（Codex 合意: 「extensions で fixture を通してから昇格」→ 通った）。

- top-level に optional **`expected_inputs`**（object）: `{"accounts": [str, ...]}`（非空・重複なし・空白のみ禁止）。
  `expected_accounts` という名前より汎用にする（他候補で account 以外の入力集合にも使える）
- `extensions.expected-accounts` は後方互換で許容。両方あるとき一致を検査
- effect item に optional **`capability_boundary`**（string）を追加（e03 で free-text へ畳んだもの）

## テスト（最低限）

- A: accepted が `bound_to` を持つ／proposal 欠落で書かれない／held に `bound_to` があれば validate が拒む／proposal を書き換えたら validate が
  「proposal drift」で FAIL／`--bundle-sha256` の形式
- B: `rereviewed` で state が変わらず page_sha256 が更新される／accepted からの rereview で `bound_to` も更新／validate の drift 基準が
  rereviewed を見る／nominated 以外（absent / held）からは拒否
- C: ok / mismatch / unverifiable の3分類／base 未指定は unverifiable／containment 違反は unverifiable／mismatch は FAIL
- D・E: schema と built-in の等価性テストに新フィールドの有効・無効例を追加
- **旧実装で赤くなること**を各群1つは確認（A の drift 検出・B の遷移・C の mismatch）
- 既存 181 passed / 33 skipped を維持（期待値を変える場合は理由をコメントに）

## 文書

- `docs/distillation-contract.md`: §6 の accepted の束縛を「CLI が自動で行う」へ、`rereviewed` を状態表と人間ゲートへ、
  `validate --refs` の3値、D/E のフィールド。**保証しないこと**（refs の unverifiable は一致ではない・rereviewed は人の明示指示でしか出ない）
- `skills/wiki-distill/SKILL.md`: `rereview` と `validate --refs` の使い方を1行ずつ

## 報告様式

結論を先に。(1) 変更ファイルと `git diff --stat`、(2) A〜E それぞれの「旧実装で赤」の確認、(3) 等価性テストの結果、
(4) この仕様に無い判断の全部、(5) 迷った点。**commit・push はしない。**
