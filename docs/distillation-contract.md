# 蒸留契約: Wiki→Skill蒸留トラック（D）

- 版: 0.2（merge 2 で `core/distill.py`＋`skills/wiki-distill/` を実装。契約本文は merge 1 から不変）
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
- `distill_id` の付与時期: 自動計数の対象は明示的に `procedure: true`・`distill_id`・review field を持つページのみ。未登録ページを人が直接 nominate する場合、**frontmatter付与と nomination event を同一 lock 内**で行う。Wikiページを持たない scheduled task は page event に押し込まず `subject_type: task`（task discovery）として記録し、正本ページ作成・review 後に page identity へ束縛する。**candidate の state を変える event（`observed / nominated / decision`）は review 済み page identity（`subject_type: page`＋`distill_id / page_path / page_sha256`）にしか発行できない**（schema で const）。task discovery は candidate state ではなく `subject_type: task` の opportunity / discovery evidence であり、正本ページ作成後は `registered` / `nominated` の page event または proposal の `source_refs`（role `task-board` / `existing-skill`・sha256 付き）からその task evidence へ辿れるようにする

- candidate identity は `(distill_id, page_path)` の**対**。state chain の途中で `page_path` が変わったら「同じ candidate」ではない（`subject identity changed` として FAIL）。`nominate` は、同じ `distill_id` に既に**別 page** の state event があるとき拒む（frontmatter を複製した page から identity を乗っ取れない）。page の移動・改名は現在の契約に無い——必要になったら専用 event を設計する（R3-3）

## 3. event（D-01・C-04・C-05・C-07）

- 置き場 `.wiki/distill/events/<event_id>.json`。**1 event 1 file・exclusive create・更新禁止**。`event_id = <UTC yyyymmddTHHMMSSZ>-<8hex>`。衝突時は乱数を引き直して最大3回まで再試行し、超えたら失敗を返す
- schema は `distill-event.schema.json`。event type 別の必須 field は `allOf` の `if/then` で閉じる（`oneOf` は使わない）:
  - **registered**（人・非 state event）: `distill_id` の付与を記録。state field を持たない
  - **observed**（`system`・自動）: 閾値到達。`absent → observed` のみ。`threshold {window_days, min_opportunities, counted_event_ids}` を必須
  - **nominated**（人）: `absent | observed | held → nominated`。`absent` 以外は `previous_event_id / previous_event_sha256` を必須
  - **decision**（人）: `nominated → held | rejected | accepted`。常に previous event を束縛。`new_state = accepted` のときだけ `bound_to {proposal{path,sha256}, effect_contract{path,sha256}, candidate_bundle{sha256}?}` を持てる（`held / rejected` では持てない・merge 3 A）。`bound_to` 自体は **optional**（R2-1）——merge 3 より前に書かれた accepted event は持たない。event は append-only なので**過去の event を後から不正にしない**。新しい CLI は必ず書く（計算に失敗したら何も書かない）
  - **rereviewed**（人）: `nominated | accepted → 同じ state`。**state を変えない**再レビューの記録で、subject の `page_sha256` を現在の実体で束縛し直す。常に previous event を束縛し、`accepted` からのときは `bound_to` も現在の proposal / effect contract で作り直す（merge 3 B）
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
| decision | human | reason, previous_event_id/sha256, expected_previous_state, new_state（accepted のときだけ bound_to を持てる・optional） | nominated → held/rejected/accepted |
| rereviewed | human | reason, previous_event_id/sha256, expected_previous_state, new_state（＝前状態。accepted のときだけ bound_to を持てる） | nominated → nominated / accepted → accepted |
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
- `rereviewed` は **state を変えない state event**（merge 3 B）。`nominated | accepted` から同じ state へ出し、`page_sha256`（と accepted なら `bound_to`）を現在の実体で束縛し直す。人がページや proposal を直して再レビューしたことを**1イベント**で記録するためのもので、`decide held` → `nominate` の2発で代用しない。**人の明示指示でしか出さない**（自動化しない・drift を自動で解消する仕組みではない）
- 人による遷移は明示操作のみ。verb: `nominate <Page>`／`status`（read-only）／`decide <distill_id> <held|rejected|accepted> --reason`／`rereview <distill_id> --reason`
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
  - `op ∈ {read, create, write, replace, delete, notify, human_action}`。`create` は存在しない path への exclusive create（既存を上書きしない）
  - write / replace には `backup` と `postcondition`、create には `postcondition`（backup は持たない・idempotency は keyed|idempotent）、delete には `actor / trigger / precondition / postcondition` に加えて `backup` **または** `irreversible_ack: true`（reversibility=none とセット・機密入力の削除など backup が redaction と矛盾する場合）、read には `freshness` / `completeness` を要求（conditional required）
  - `local_io`（kind / sensitivity / retention / redaction すべて必須・output にも適用）/ `network` / `credential_locator` は resource 種別に応じた conditional required。削除は read の retention 記述に埋めず**独立 effect として宣言**する
- 未宣言 effect は validation fail、既知でない class は schema error
- top-level の任意 field（merge 3 E）: `expected_inputs {accounts: [str, …]}`（非空・重複なし・空白のみ禁止）は「実行前に揃っているべき入力集合」の invariant。候補(a) の `extensions.expected-accounts` を昇格したもので、account 以外の入力集合にも使えるよう汎用名にした。`extensions.expected-accounts` は後方互換で許容し、**両方あるときは top-level が正**で validator が食い違いを報告する。effect item の `capability_boundary`（free text）は、その effect が触ってよい範囲の宣言（宣言であって runtime の enforcement ではない）

## 6. 人間ゲート適用表（D-05・C-08）

| ゲート | 何を承認するか | 束縛先 | 適用 |
|---|---|---|---|
| nomination | 候補入り（実行承認ではない） | `distill_id`・page sha256 | 常に（候補ごと1回） |
| candidate validation（accepted） | proposal・effect contract・sandbox evidence | proposal hash・effect-contract hash（＋任意で candidate bundle hash）。**CLI が `decide accepted` 時に自動で計算し `bound_to` へ入れる**（理由文への手書きに頼らない。計算に失敗したら何も書かない＝**新しい CLI からは** hash 無しの accepted は出ない）。**`bound_to` の path はその candidate 自身の `distill/<slug>/proposal.md` / `effect-contract.json` でなければならない**（hash が実体と一致するだけでは「何を承認したか」を保証しない。比較は正規化した portable path の等値で、「解決すると同じ file に着く」は根拠にしない・R3-1/R3-2）。`bound_to` を持たない accepted は、**merge 3 導入前に CLI が書いた既知の event の固定リスト（`LEGACY_ACCEPTED_EVENTS`・`event_id → file bytes の sha256`・core の定数・増やすには commit が要る）**に id と sha256 の両方が一致するものだけ `legacy` として報告する（FAIL にしない）。それ以外の束縛無し accepted は FAIL——`occurred_at` は書き手が決められる値なので時刻の窓では判定しない（R5/G15。窓だと cutoff 前の chain に backdate した accepted を繋いで legacy に化けさせられた。id だけの照合も、別の Vault でその名前の file を書けば通るので sha256 を添える・R5/G19）。legacy の正規の解消は共有 Vault で `rereview` を実行したあと、この固定リストの entry を外す commit。束縛し直す正規の経路は **`rereview`**（人の明示指示） | release ごと |
| deployment decision | 配備 transaction の実行 | manifest version・candidate bundle hash・runtime hash・effect-contract hash・wiki ref hashes | release ごと |
| production authorization | 本番 run 可 | 同上＋`state=deployed` | **effect/risk 別**: `external_replace/delete` は release ごと＋初回 attended run；`human_action_required` 型は各 run の入力提供を run 承認と扱う；read-only は schedule enabled 自体を継続承認とし、N/A は理由付きで記録（silent skip 禁止） |

decision は event（lifecycle）と manifest `decision` の両方に残し、発話は `given_via` に引用する。

## 7. drift の意味（D-05）

Wiki ref / runtime ref / candidate tree の drift は manifest state を書き換えない（自動失効なし）。**ただし再レビュー完了まで check / production 入口は fail-closed**。「自動失効なし」≠「実行継続可」。

`distill validate` が見る drift は3種類で、いずれも state を書き換えず FAIL にする:

- **page drift**: `page_sha256` を束縛した**最新の state event**（`nominated` / `decision` / `rereviewed`・順序は `previous_event_id` の連鎖）と現在の bytes の不一致
- **proposal drift / effect-contract drift**: accepted の head の `bound_to.{proposal,effect_contract}.sha256` と現在の実体の不一致（実体が消えていても drift 扱い＝fail-closed）。**`bound_to` が無い accepted（legacy）では drift を検査しない**——「検査していない」を「一致」と混ぜないため、validate は `legacy=N` として別に数える
- **ref mismatch**（`validate --refs`）: proposal の `source_refs[]` / `effect_contract` の宣言 hash と実体の不一致

`distill validate` は chain 自体にも次を要求する（drift ではなく即 FAIL）:

- **chain の時刻単調性**（R4-2）: state chain 上で `occurred_at` は**直前の event 以上**であり、各 event の `event_id` の時刻 prefix（`YYYYMMDDTHHMMSSZ`）は `occurred_at` と**同じ秒**であること。非 state event（`opportunity` 等）も同じ時刻の整合を要求する。`occurred_at` は書き手が決める値なので、legacy 判定の材料にはしない（固定リスト・上記）。**CLI は書く前に chain head の時刻と比較**し、新 event の時刻が head より前なら、ずれが 300 秒以内は head と同じ秒へ揃えて書き（「head より前ではない」という事実だけを記録する。token は保つ）、それ以上は時計の異常として拒む（R5/G18。書けてしまってから validate が落ちると append-only の store は CLI で直せない）。衝突 retry は時刻 prefix を保って token だけを引き直す（R5/G17）
- **非 state event の subject identity**（R4-3）: 非 state event の `subject.page_path` は chain の page identity と一致すること（違えば「subject identity changed」）。drift 検査が見る page identity（`page_path` / `page_sha256`）は **state chain の head** から採る（非 state event の subject に引きずられない）。path の等値比較は **ASCII の A–Z だけを畳んだ**正規形で行う（`str.lower()` / `os.path.normcase` は U+212A KELVIN SIGN などを NTFS と違う形で畳み、別 file を「同じ path」と見せるため使わない・R4-1）

解消は人の再レビューだけ（`distill rereview`）。**保証しないこと**: **head event（chain の末尾）の in-place 書き換えは、次の event が書かれるまで検出を保証しない**——head の sha は誰の `previous_event_sha256` にも参照されない（append-only store の構造的限界）。`distill/_index.md` に head の `event_id` と `sha256` を持たせているので「書き換えたが `distill reindex` を忘れた」ケースは index 不一致として拾えるが、書き換えた本人が reindex すれば index も追随する。次の state event（`decide` / `rereview`）が書かれた時点で、その event の `previous_event_sha256` が錨になる。 `--refs` の `unverifiable` は「一致」ではなく「検証していない」（base 未指定・containment 違反・実体を読めない場合）。`source_refs` の mismatch は再レビューの trigger であって、実行前に効果を封じる gate ではない（runtime ref の fail-close は別・§5 の保証上限）。

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
- resolver の責務（merge 2）: `base_id` を canonical resolve し、`portable_path` を結合した**解決後 path が base 配下であること**を比較する（`..` 以外にも symlink / junction 経由の base 脱出を fail-closed。pilot の lexical 一致＋reparse 検査と同じ規律）。schema の `portable_path` は字句検査（segment 単位で `.`・`..`・空・制御文字・バックスラッシュ・ドライブ文字・`~` を拒否）に留まる
- proposal（`distill/<skill_slug>/proposal.md`）の追加規則（merge 3 D）: top-level の任意 field `document_revision`（`^\d+\.\d+$`）と `supersedes`。`proposal_version` は **schema 版の const** であって文書の改訂番号ではない。`extensions.revision` は後方互換で許容し、**両方あるときは top-level が正**で validator が食い違いを報告する
- proposal frontmatter は **YAML の限定サブセット**として読む（`parse_frontmatter_yaml_subset`・R2-2）。人が書くブロック形式の入れ子（`source_refs` / `effect_contract` / `extensions` / `hosts` / `extracted_requirements` / `bundle_scope` / `sensitive_inputs` / `review`）をそのまま読み、1行 JSON flow（R1 の書き方）も部分集合として通る。**対応する構文**: ブロック mapping / ブロック sequence（要素は scalar でも mapping でもよい）/ プレーン・ダブルクォート（`\"` `\\` `\n` `\t` `\r` `\/`）・シングルクォート（`''`） scalar / 1行の flow（`[a, b]` `{k: v}`。1行 JSON はそのまま）/ クォート外の `#` コメント / `true false null 整数 小数` の型付け（**日付は文字列のまま**。PyYAML の date 化を再現しない）。**対応しない構文**（anchor / alias / tag / 複数ドキュメント / `|` `>` のブロック scalar / 複数行 flow / タブインデント / インデント不整合 / key 重複 / 1行 JSON の key 重複・NaN・Infinity・深さ 8 超）は **`unparseable`** として報告し、その proposal の refs は `unverifiable` にし、**`decide accepted` はそもそも通さない**（unparseable な proposal を束縛対象にしない・R3-5）——**黙って部分的に読まない**。クォート判定は**値の先頭文字が `'` / `"` のときだけ**行う（`note: it's fine` は plain scalar・R3-6）。桁や表記が意味を持つ plain scalar——64桁の数字列（sha256）・先頭 0 の数字列・`^\d+\.\d+$`（版番号）——は**文字列のまま**にする（数値化すると復元できないため。`0.1` と `0.10` は別の版・R3-7/R3-11）。`skill_slug` / `distill_id` として読む値は常に文字列として扱う（R3-15）。判定の正本は built-in（外部 YAML 実装に依存しない）。`skill_slug` は **directory 名と一致**していなければならない（別 candidate の hash へ向ける偽装を拒む）
- 単一 JSON Schema では表せない invariant（validator の責務として明記）: 1 opportunity に terminal 最大1つ（event 集合全体）、observation の各 count と閾値の比較（`pass` は3条件すべて達成時のみ）、`decision.bound_to` の各 hash が同版の実値と一致すること（`bound_to` を持つ accepted のみ。持たない legacy は `legacy` として数え、FAIL にしない）、`wiki_refs` 集合が approved decision の `wiki_ref_sha256s` と一致すること、`state=rejected` の decision が reject であること、accepted の head が束縛した proposal / effect-contract hash が現在の実体と一致すること（§7 の drift）、`bound_to` の path が**その candidate 自身**の proposal / effect contract であること（R3-1）、state chain 上で `subject.page_path` が変わらないこと（R3-3）、state chain 上で `occurred_at` が非減少で `event_id` の時刻 prefix と同じ秒であること（R4-2）、非 state event の `subject.page_path` が chain の page identity と一致すること（R4-3）、`validate --refs` の実体照合（ok / mismatch / unverifiable の3値。宣言 hash が**数値**で書かれていたら桁を復元できないので `unverifiable` ではなく mismatch＝FAIL。path の解決は `wiki/` `distill/` が Vault root 配下、それ以外は先頭 segment を base id とみなし `--ref-base <id>=<dir>` で与えられた base 配下へ解決し、字句＋実解決で containment を確認する）
- schema 自体が閉じる invariant（merge 1 で実装済み）: `decision.status ∈ {approved, rejected}` なら `by / at / reason / given_via / bound_to`（全 field）必須／`state ∈ {deployed, deprecated}` なら approved decision と非 null `deployed_bundles`／`state = rejected` なら reject decision／`proposed | validated` では `deployed_bundles = null`／`verdict ∈ {pass, fail}` なら監査 field 必須・`terminal_conflicts = 0`／`pass` は `eligible ≥ 1`・`completed ≥ 1`・`unterminated = 0`／最低件数は 1 以上

## 11. 共通の ID / hash 規則

- hash は SHA-256 hex 64桁。bundle hash は `logical_path\0sha256` を logical path 昇順で `\n` 連結した文字列の SHA-256（pilot と同一）
- 時刻は UTC `YYYY-MM-DDTHH:MM:SSZ`。ID の乱数部は `secrets.token_hex(4)`
- path は Vault / repo 相対・`/` 区切り。環境固有の絶対 path を record に書かない。manifest の `wiki_refs` は絶対 path の代わりに **portable な `base_id`**（例 `vault`）を持ち、実 path への解決は host 設定側で行う。schema は `portable_path`（ドライブ文字・先頭 `/`・`~`・バックスラッシュに加え、segment 単位の `.`・`..`・空 segment（`//`・末尾 `/`）・制御文字（C0 U+0000–U+001F・DEL U+007F・C1 U+0080–U+009F＝Unicode Cc 全域）を禁止。`<run_id>` のような placeholder・日本語・空白は許す）で機械的に拒否し、base 配下の containment は resolver（§10）が再検査する
