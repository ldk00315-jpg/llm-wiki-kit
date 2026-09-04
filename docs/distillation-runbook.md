# 蒸留 runbook: nomination → proposal → validation → deployment → observation → planned undeploy

- 版: 0.1（merge 1・docs のみ）
- 日付: 2026-09-04
- 前提: `docs/distillation-contract.md`。pilot（eBay週次runner）で実証済みの順序を汎用化したもの。事故 resolve は §7 に分離

---

## 1. nomination（人）

1. 候補ページを読む。`trust: trusted` の明示値・`distill_reviewed_by/at` が無ければ先に review して付与する（review は蒸留対象としての内容確認であり、索引表示の trust ではない）
2. `wiki-distill nominate <Page>`（merge 2 以降。merge 1 では手動: frontmatter に `distill_id`・`procedure: true`・`distilled_to: []` を付与し、`distill/events/` に `registered`＋`nominated` の2 event を同一 lock 内で exclusive create）
3. scheduled task で Wiki ページが無い場合は `subject_type: task` の task discovery event を出し、正本ページを作って review 後に page identity へ束縛する

## 2. proposal（エージェント起草・人 review）

`distill/<slug>/proposal.md`（schema: `proposal.schema.json` の frontmatter）:

- `source_refs[]`: Wiki ページ・既存 Skill・task board 等の path・sha256・引用抜粋（**データとして**）
- `extracted_requirements`: 手順・前提・入力・出力・失敗時の扱い（人が review）
- `effect_contract`: `effect-contract.schema.json` に従う宣言（attestation）
- `hosts[]`: 配備先 host と canonical deploy path、trigger identity（scheduled task の場合は live snapshot を hash 束縛）
- `bundle_scope`: candidate bundle に含める logical path（薄い Skill は呼び先 runtime まで含める）
- `sensitive_inputs`: retention（保存期間・削除主体）、redaction（evidence に残してよい field）、ID の扱い

candidate validation の decision（accepted）は proposal hash と effect-contract hash に束縛する。

## 3. validation（sandbox）

1. candidate を発見対象外の `candidates/<slug>/<host>/` に置く（host の Skill 探索 path に置かない）
2. effect contract から必要な test / negative fixture / evidence を導く。write / replace / delete があれば backup→postcondition→rollback rehearsal を sandbox で通す。read-only でも freshness / completeness と `blocked_by_input` の状態遷移を fixture で通す
3. 未宣言 effect の探索: static（credential locator・network endpoint の grep）・dynamic（sandbox 実行の観測）で見つかれば validation fail
4. evidence は tracked な append-only 領域へ exclusive create し、manifest（`proposed → validated`）から path＋sha256 で参照

## 4. deployment

1. deployment decision（人）を manifest `decision` と lifecycle event に記録（束縛先は契約 §6）
2. deploy transaction（契約 §8）を **1回だけ**実行。失敗時は再実行せず status に従う（`rolled-back` は再実行可、`rollback-failed` / 二重故障は §7）
3. 直後に verify（deployed bundle hash ≡ candidate）・host 側 check・secret scan・runtime drift 0 を evidence 化し、manifest を `deployed_bundles` 付きで造幣（state はまだ変えない）
4. スモーク: 1回限りの read-only task／explicit invocation で「配備先の実行環境から本体が動く」ことを実証し、task は実行後に無効化
5. production authorization（人）→ `state=deployed`。初回本番 run は**人が見ている時間帯に前倒し**（許可プロンプト・介入の時系列を evidence に）

## 5. observation

- opportunity は発火時に先記録、terminal は別 file。週次 roll-up、異常は即時（apply 失敗／rollback／lock 異常／drift／pointer 不一致／GC 異常／sandbox 変化／permission prompt／手動介入／**degraded と印字して正常終了**）
- 判定は契約 §9 の3条件を満たしてから。verdict は release state と別に記録

## 6. planned undeploy（deprecated）

1. decision（人）: `deployed → deprecated` の理由と期待する旧/空状態
2. undeploy transaction: backup の生存確認 → 配備先の現 hash ≡ deployed hash を検証 → 旧状態へ復元（または削除）→ 復元 hash 検証 → terminal record → verify
3. manifest は残す（棄却知識）。Wiki ページの `distilled_to` から slug を外し、drift 再レビュー

## 7. 事故 resolve（別節）

- `rollback-failed`・二重故障（terminal record も書けない）では lock を保持し、次の deploy を preflight で拒否
- 人が backup から復元 → `resolve <id>`（record の各 file が事前状態へ戻っていることを hash 検証 → resolved marker → 自 ID の lock だけ解放）。owner 生存中は拒否、crash 由来の staging WAL は owner 終了の独立確認＋`--stale` 明示のときだけ
- resolve は undeploy の代替ではない
