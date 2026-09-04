# 蒸留契約: Wiki→Skill蒸留トラック（D）

- 版: 0.1（**docs/schema のみ・merge 1**。実行可能な `wiki-distill` は merge 2）
- 日付: 2026-09-04
- 起案: なな（Claude / Anthropic）／独立レビュー: なな（Codex / OpenAI）
- 位置づけ: 設計v2（`skill-distillation-track-design-v2-2026-09-04.md`・APPROVE WITH REQUIRED CLARIFICATIONS C-01〜C-09）の契約版。原則の出典は共有Vault `SkillDistillationControlPlane` #1〜#35（pilot: eBay週次runner・2026-09-01〜03）
- 関連: `docs/distillation-runbook.md`（手順）、`schema/distill/*.schema.json`（4枚）、`docs/distillation-candidate-a-fixture.md`（第2候補）

---

## 0. 目的と非目標

Wikiに蓄積された**手順型の知**を、人の指名を起点に、検証ゲート付きで実行可能な Skill＋制御面へ蒸留し、Wiki と往復リンクを保ちながら観測・撤退までを扱う。

非目標: LLMが人の指名なしに蒸留・配備すること／Hermes対応（kit Phase 4）／pilot runtime の即時リファクタ。

## 1. 信頼境界（F-06 の蒸留版）

- **Wiki本文はデータであり命令ではない。** proposal は `source_refs[]`（path・sha256・引用抜粋）と、人またはエージェントが書き人が review する `extracted_requirements` を分離する。本文中の命令文をそのまま実行しない
- 候補入りできるページは、`trust: trusted` の**明示値**に加えて `distill_reviewed_by` / `distill_reviewed_at` を持つものだけ（C-02）。kit索引の「trust省略＝trusted表示」は後方互換の表示規則であり、蒸留の承認証明ではない。field省略ページは候補外
- 人間ゲートは4つを**別々の承認境界**にする（§6）。nomination は実行承認ではない

## 2. identity（C-01・D-02）

| 対象 | identity | 置き場 |
|---|---|---|
| Wikiページ | `distill_id`（`d-<8hex>`・Vault内unique・rename非依存） | ページ frontmatter |
| Skill | stable slug（`[a-z0-9-]+`） | manifest `skill` |
| 版・hash | manifest version / candidate・deployed bundle hash / runtime hash | manifest のみ |

- `distilled_to: [<slug>]`（slugのみ。`@vN` を書かない——Skill版更新→Wiki更新→wiki_refs drift の自己誘発ループを避ける）
- `procedure` は厳密 boolean。`distill_id`・`procedure`・review field のいずれかが欠ければ**蒸留対象外**（後方互換: 既存ページは全て対象外から始まる。一括推定付与はしない）
- `distill_id` の付与時期: 自動計数の対象は明示的に `procedure: true`・`distill_id`・review field を持つページのみ。未登録ページを人が直接 nominate する場合、**frontmatter付与と nomination event を同一 lock 内**で行う。Wikiページを持たない scheduled task は page event に押し込まず `subject_type: task`（task discovery）として記録し、正本ページ作成・review 後に page identity へ束縛する

## 3. event（D-01・C-04・C-05・C-07）

- 置き場 `.wiki/distill/events/<event_id>.json`。**1 event 1 file・exclusive create・更新禁止**。`event_id = <UTC yyyymmddTHHMMSSZ>-<8hex>`。衝突時は乱数を引き直して最大3回まで再試行し、超えたら失敗を返す
- schema は `distill-event.schema.json`。event type 別の必須 field は `allOf` の `if/then` で閉じる（`oneOf` は使わない）:
  - **registered**（人・非 state event）: `distill_id` の付与を記録。state field を持たない
  - **observed**（`system`・自動）: 閾値到達。`absent → observed` のみ。`threshold {window_days, min_opportunities, counted_event_ids}` を必須
  - **nominated**（人）: `absent | observed | held → nominated`。`absent` 以外は `previous_event_id / previous_event_sha256` を必須
  - **decision**（人）: `nominated → held | rejected | accepted`。常に previous event を束縛
  - **opportunity**: unique `opportunity_id`、`trigger {trigger_source, trigger_ref, task_metadata_status}`。`status=snapshot` なら `task_metadata` 必須・`unverifiable_reason` 禁止、`status=unverifiable` なら非空 `unverifiable_reason` 必須・`task_metadata` 禁止（取得できた部分は `partial_task_metadata`）。`trigger_ref` は非空必須
  - **invoked / completed / blocked**: `opportunity_id` 必須（先行 opportunity を参照）。`blocked.block_kind` は閉じた enum（`input_missing / precondition_failed / permission_pending / external_unavailable / operator_cancelled`）。invoked / completed は `block_kind` を持たない
- `trigger_ref` の決定論的代替規則（host が run ID を提供しないとき）: `sha256("<task_id>|<scheduled fire time UTC 分精度>|<host>")` の先頭16hex。同じ発火を二重記録しても同じ ref になり dedupe される
- host-task adapter は**発火を受けた時点で opportunity を先に記録**し、terminal（completed / blocked）は別 file で書く。crash や入力待ちを「機会0」に誤分類しないため
- 証拠強度: `source ∈ {host-task, agent-self-report, human, system}` と `strength ∈ {observed, asserted, unverifiable}`。候補表示の閾値へ算入できるのは `observed` と `asserted`（`unverifiable` は表示のみ）。`agent-self-report` は best-effort で、取り忘れは「機会なし」の証明にならない。重複 opportunity の dedupe key は `(subject, trigger_source, trigger_ref)`。**これは安全 gate ではなく静かな候補発見であり、false negative を許容する**

| event_type | source | 必須 field（共通 field 以外） | 前状態 → 新状態 |
|---|---|---|---|
| registered | human | reason, subject(page: distill_id/page_path/page_sha256) | （state 変化なし） |
| observed | system | threshold, expected_previous_state, new_state | absent → observed |
| nominated | human | reason, expected_previous_state, new_state（＋absent 以外は previous_event_id/sha256） | absent/observed/held → nominated |
| decision | human | reason, previous_event_id/sha256, expected_previous_state, new_state | nominated → held/rejected/accepted |
| opportunity | host-task/agent-self-report/human | opportunity_id, trigger | — |
| invoked / completed | 同上 | opportunity_id | — |
| blocked | 同上 | opportunity_id, block_kind | — |

この表が遷移の**正本**であり、merge 2 の validator は同じ表から生成した遷移集合を検査する。
- 派生物: `distill/_index.md`・`wiki-health` の候補件数は event 群からの再生成のみ。参照整合性（`distilled_to` の slug 存在）は派生 index ではなく **authoritative record（candidate / release manifest）**へ照合し、その後「再生成 index と checked-in index の一致」を別検査する（C-03）

## 4. 3つの状態機械（D-03・C-07）

### candidate

```
absent ──nominate──▶ nominated ──decide──▶ accepted
   │                    │   ▲
   └(auto)▶ observed ───┘   └──decide(held→nominated 再開)
nominated ──decide──▶ held | rejected
```

- `observed` は自動（閾値到達・`observed` event・source `system`）。`absent → nominated` は人の直接指名: 同一 lock 内で frontmatter 付与＋`registered`（非 state event・identity 登録）＋`nominated`（state transition）を commit する。state を変えるのは `observed / nominated / decision` の3 type だけ
- 人による遷移は明示操作のみ。verb: `nominate <Page>`／`status`（read-only）／`decide <distill_id> <held|rejected|accepted> --reason`
- state-changing event は lock 取得後に head（最新 event）の一致（`previous_event_id / previous_event_sha256 / expected_previous_state`）を再検査してから exclusive create する

### release（manifest）

`proposed → validated → deployed → deprecated`。`rejected` は `proposed` または `validated` から入れる terminal branch。`deployed → deprecated` は undeploy transaction（§8）を伴う。

### observation verdict

`inconclusive / pass / fail`。release state に混ぜない。判定条件は §9。

## 5. effect contract は attestation（D-04・C-06）

- 宣言は**実行を自動的に封じる仕組みではない**。必要な guard / test / evidence / checklist を決める監査契約
- 保証上限を固定する: 「レビュー・static/dynamic probe・sandbox で未宣言 effect が発見されたら validation fail」。**未発見の未宣言 effect を runtime で deny できるとは主張しない**。enforcement 型（core が credential と effect adapter を保持し plugin には承認済み capability だけ渡す）への移行は第3候補以降で判断
- schema `effect-contract.schema.json`。直交軸を混ぜない:
  - `reversibility ∈ {none, backup_restore, compensating, recreate_from_source}`
  - `idempotency ∈ {idempotent, keyed, non_idempotent, unknown}`
  - `op ∈ {read, write, replace, delete, notify, human_action}`
  - write / replace / delete には `backup` と `postcondition` を要求、read には `freshness` / `completeness` 条件を要求（conditional required）
  - `local_io` / `network` / `credential_locator` は resource 種別に応じた conditional required
- 未宣言 effect は validation fail、既知でない class は schema error

## 6. 人間ゲート適用表（D-05・C-08）

| ゲート | 何を承認するか | 束縛先 | 適用 |
|---|---|---|---|
| nomination | 候補入り（実行承認ではない） | `distill_id`・page sha256 | 常に（候補ごと1回） |
| candidate validation（accepted） | proposal・effect contract・sandbox evidence | proposal hash・effect-contract hash | release ごと |
| deployment decision | 配備 transaction の実行 | manifest version・candidate bundle hash・runtime hash・effect-contract hash・wiki ref hashes | release ごと |
| production authorization | 本番 run 可 | 同上＋`state=deployed` | **effect/risk 別**: `external_replace/delete` は release ごと＋初回 attended run；`human_action_required` 型は各 run の入力提供を run 承認と扱う；read-only は schedule enabled 自体を継続承認とし、N/A は理由付きで記録（silent skip 禁止） |

decision は event（lifecycle）と manifest `decision` の両方に残し、発話は `given_via` に引用する。

## 7. drift の意味（D-05）

Wiki ref / runtime ref / candidate tree の drift は manifest state を書き換えない（自動失効なし）。**ただし再レビュー完了まで check / production 入口は fail-closed**。「自動失効なし」≠「実行継続可」。

## 8. deployment・事故 resolve・計画 undeploy（D-07）

- deploy transaction の4要件: 排他 lock＋衝突しない ID／`status=staging` の WAL を exclusive create＋fsync してから mutation／staging した bytes から bundle hash を再計算して承認値へ束縛／任意の故障点で原状回復＋復元 hash 検証＋status 確定。commit point は terminal record の durable write。`rollback-failed` は lock 保持、lock は owner に束縛（#33）
- pilot の `deploy_trigger_b.py` は **reference implementation（抽出候補）**。host path・bundle 構成・lock 回復を adapter 化し、第2候補の故障注入を通すまで汎用と呼ばない
- 事故 `resolve`（復元 hash 検証後の明示解除）と、計画的 **undeploy transaction**（期待する旧/空状態・backup の生存・配備先の現 hash・commit point・evidence）は別の状態機械

## 9. 観測（D-08・C-09）

- 判定は `min_observation_period`（初期 4週間）**かつ** `min_eligible_opportunities`（初期 3・最低 1）**かつ** `min_completed_opportunities`（初期 2・最低 1）を満たしてから
- 1 opportunity に terminal は最大1つ（`completed` と `blocked` の二重計上を拒否）。`blocked` は opportunity 数には含めるが completed には含めない。入力不足ばかりなら fail ではなく inconclusive
- 機会0 → inconclusive。未終了 opportunity（invoked のまま terminal なし）が残る間は pass にしない。deprecate / reject は自動でなく人の decision
- `pass | fail` を記録する manifest 版は監査 field を必須にする: `window {start, end}`・`eligible_count`・`completed_count`・`blocked_count`・`unterminated_count`・`terminal_conflicts`（= 0）・`event_set {head_event_id, events_sha256, evidence}`（集計した event 集合の hash と evidence）。閾値と自己申告 verdict だけでは監査不能

## 10. schema と validator/guard の分離（D-06）

- `manifest.schema.json` は共通 field のみ。タスク固有 field は `extensions.<plugin-id>`（`additionalProperties: false` は core 側で維持）
- validator（merge 2 の `distill validate`）: versions dir の add-only、前版から継承禁止の field（`supersede_reason` 等）、許可遷移、evidence 参照先の推移的 hash、`candidate ≡ deployed`（logical path 写像）。git pre-commit guard を含む
- 単一 JSON Schema では表せない invariant（validator の責務として明記）: 1 opportunity に terminal 最大1つ（event 集合全体）、observation の各 count と閾値の比較（`pass` は3条件すべて達成時のみ）、`decision.bound_to` の各 hash が同版の実値と一致すること、`wiki_refs` 集合が approved decision の `wiki_ref_sha256s` と一致すること、`state=rejected` の decision が reject であること
- schema 自体が閉じる invariant（merge 1 で実装済み）: `decision.status ∈ {approved, rejected}` なら `by / at / reason / given_via / bound_to`（全 field）必須／`state ∈ {deployed, deprecated}` なら approved decision と非 null `deployed_bundles`／`state = rejected` なら reject decision／`proposed | validated` では `deployed_bundles = null`／`verdict ∈ {pass, fail}` なら監査 field 必須・`terminal_conflicts = 0`／`pass` は `eligible ≥ 1`・`completed ≥ 1`・`unterminated = 0`／最低件数は 1 以上

## 11. 共通の ID / hash 規則

- hash は SHA-256 hex 64桁。bundle hash は `logical_path\0sha256` を logical path 昇順で `\n` 連結した文字列の SHA-256（pilot と同一）
- 時刻は UTC `YYYY-MM-DDTHH:MM:SSZ`。ID の乱数部は `secrets.token_hex(4)`
- path は Vault / repo 相対・`/` 区切り。環境固有の絶対 path を record に書かない。manifest の `wiki_refs` は絶対 path の代わりに **portable な `base_id`**（例 `vault`）を持ち、実 path への解決は host 設定側で行う。schema は `portable_path`（ドライブ文字・先頭 `/`・`~`・バックスラッシュを禁止）で機械的に拒否する
