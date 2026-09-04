# 第2候補(a) `monthly-listing-recon-csv` — brownfield onboarding fixture（案）

- 版: 0.1（merge 1・docs のみ。実 fixture は merge 2 の tests へ）
- 日付: 2026-09-04
- 目的: **既存 scheduled Skill の蒸留トラック取り込み**。Wiki→新 Skill 生成経路の実証ではない（それは後続の net-new 候補＝手動復旧レシピ型で行う）
- 前提: `docs/distillation-contract.md`・`docs/distillation-runbook.md`

---

## 1. 3者差分（source_refs）

| source | role | 取り込み方 |
|---|---|---|
| 既存 scheduled Skill `~/.claude/scheduled-tasks/monthly-listing-recon-csv/SKILL.md`（sha256 `5bd03a38…`・Codex 確認値。実装時に再取得） | existing-skill | candidate の baseline。本文を `excerpt` としてデータ保存 |
| OpenLister task board「月1・Seller Hub CSV を人が取得・未監視ギャップのみ」 | task-board | 運用の根拠。sha256 は export 時点 |
| Wiki `OpenListerReconciliationGhosts` の一節 → **正本ページ `MonthlyListingReconCsv` を切り出す**（review 後 `distill_id` 付与） | wiki-page | extracted_requirements の出典 |

差分の検査: 3者で「手順の順序」「入力（CSV の種類・取得元）」「出力（report の形・通知先）」「失敗時の扱い」が一致しない箇所を列挙し、proposal の `extracted_requirements` で1つに確定する（人が review）。

## 2. trigger identity

- `task_id: monthly-listing-recon-csv`・`cron 0 10 23 * *`（local TZ Asia/Tokyo・jitter 183s・enabled・次回 2026-09-23）。manifest へ採る際は **live snapshot を再取得して hash 束縛**（`status: snapshot`／取得不能なら `unverifiable`）
- 同名二重入口を作らない: Trigger-B と同じく既存 task の SKILL.md を candidate 本体で**置換**（task ID 不変）

## 3. bundle_scope（Codex 指摘への回答）

既存 Skill は手順文で専用 runner を持たない。**薄い Skill の呼び先まで bundle identity に含める**方針で、bundle_scope は次の2案から fixture で決める:

- 案1（最小）: `claude/SKILL.md` のみ。手順中で呼ぶ既存 script（CSV parser・DB query・report generator）が repo 内にあれば `runtime_refs` として hash 追跡
- 案2（推奨）: `claude/SKILL.md`＋`codex/SKILL.md`＋呼び先 script を logical path に含める（pilot と同じ「呼び先 runtime まで bundle」）

fixture では案2で、script の hash 改変が `check` の drift として検出されることを負の fixture にする。

## 4. effect contract（宣言案）

| id | resource | op | 主な条件 |
|---|---|---|---|
| e01 | human | human_action | Seller Hub から Active Listings CSV を人が取得して所定 path へ置く（`human_action: required`）。未着は `blocked(input_missing)` |
| e02 | local-file | read | 機密 CSV（sensitivity high）。`freshness`: mtime が当月・`completeness`: 行数>0 と必須列。`retention`: 突合完了後 N 日で削除（削除主体: Skill 本体・evidence には行数と hash のみ）。`redaction`: ItemID は evidence に残さず hash 化、価格・タイトルは残さない |
| e03 | local-db | read | OpenLister DB（read-only）。`freshness`: 取得時刻・`completeness`: active 件数 |
| e04 | local-file | **create** | 突合 report を run 別 path へ **exclusive create**（既存を上書きしない。`reversibility: recreate_from_source`・`idempotency: keyed`・`postcondition`: 件数と schema）。**output も機密**: `local_io {kind: output, sensitivity: high, retention: 90日・削除主体は e07, redaction: tracked evidence には report の sha256 と件数のみ・ItemID/タイトル/価格は残さない}` |
| e05 | notification | notify | とんすけへの結果通知（`idempotency: keyed`・重複通知を dedupe key で防ぐ） |
| e06 | local-file | **delete** | 入力 CSV の retention enforcement。`actor: skill`・`trigger: 次回 run の prepare 時（completed 済みの前回 input）または blocked のまま 30 日経過`・`precondition: 対象 run の terminal が completed または blocked≥30日、かつ report が存在`・`reversibility: none`＋`irreversible_ack: true`（機密入力は backup しない＝redaction と整合）・`postcondition: 対象 path 不在・削除 event を record に記録`・対象 glob `runs/<run_id>/input/*.csv` |
| e07 | local-file | **delete** | report の retention enforcement（90日）。`actor: skill`・`trigger: prepare 時`・`precondition: created+90日`・**`reversibility: none`＋`irreversible_ack: true`**（90日時点では同月の入力 CSV は e06 で消えており再生成できないため、正直に不可逆として宣言する。backup は取らない＝機密 output の redaction と整合）・`postcondition: 対象 path 不在・削除 event` |

e01〜e07 は `tests/test_distill_schemas.py` の **1件の contract instance**として validate される（fixture 本文と test data の対応は test 内のコメントで固定。未検査 effect を残さない）。

external write なし。`concurrency.lock: pipeline-wide`。削除は独立 effect（e06/e07）として宣言し、read (e02) の retention 記述はそれを参照する。

## 5. 人間入力待ちの状態遷移（host-neutral fixture）

```
opportunity(scheduled 発火・先記録) → invoked
   ├─ CSV あり → completed（report・notify）
   └─ CSV なし → blocked(input_missing)  … terminal は1つだけ。completed と二重計上しない
```

- fixture: (1) CSV あり→completed、(2) CSV なし→blocked、(3) CSV が古い（前月）→blocked(precondition_failed)、(4) 同一 opportunity に completed と blocked を両方書こうとする→拒否、(5) crash（invoked 後に terminal なし）→観測集計で「未終了」として扱い completed に数えない

## 6. 観測条件

月次のため `min_observation_period_days: 28` だけでは最大1機会。`min_eligible_opportunities: 3`・`min_completed_opportunities: 2` を併用（＝実質3か月）。入力未提供が続けば inconclusive。

## 7. 機密 CSV の retention / redaction（具体）

- 保存: `runs/<run_id>/input/active-listings.csv`（gitignore・ACL は人が設定）
- 削除主体: **Skill 本体（effect e06）**。突合完了（completed）後、次回 run の prepare 時に前回 run の input を削除（削除 event を record に残す）。blocked のまま放置された input は 30 日で削除。backup は取らない（`irreversible_ack: true`・redaction と整合）
- evidence に残してよい: 行数・列名・sha256・取得日。残さない: ItemID（hash 化のみ）・タイトル・価格・数量
- report（e04 output）: 突合結果は ItemID を含む（人が読む用）ため **output も sensitivity high**。retention 90日（effect e07 が削除）。tracked evidence には report の sha256 と件数のみ。入力だけを機密扱いして output を無期限に残す抜け道を作らない
