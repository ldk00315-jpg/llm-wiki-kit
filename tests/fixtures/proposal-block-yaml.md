---
proposal_version: "0.1"
extensions:
  revision:
    document_revision: "0.10"
    supersedes: "0.9 (2026-09-07)"
    reason: "0.10: e14 の書き換えを tmp/fsync/replace の原子置換へ、e13 の検証範囲を実装に合わせて更新。運用変更: Codex 外部レビュー終了（2026-09-08・とんすけ決定）、以後は Claude 内部監査（Opus 5 実装 → Fable 5.1 攻撃側 → なな）と人間 gate。0.9: Codex L2 R5-C01〜03（入口 run の junction・post 永続化・note 到達）。コード側の終了条件（次の L2 で P1 が 0 なら終了）を台帳に明記。0.8: Codex L2 R4-C01〜03（内向き junction・finalize 再開不能・当月の掃除再試行）と留保2件を契約へ反映。0.7: Codex R2-01〜03（report の有無で状態判定・opportunity 孤立・削除記録の再実行）を受け、状態機械を recon_gap.py prepare/finalize/retire へ実装。terminal.json を e14 として宣言し、e13 を attempt ごとに、保持起点を started_at へ統一。0.6: Codex L1-01/02（blocked 早期終了が掃除を迂回・scan/verification に削除先が無い）を受け、e06/e07/e08 の対象と起点（run.json の started_at）を定め、e12 tracked evidence・e13 cleanup evidence を追加。0.5: effect contract に scan.json / verification.json / opportunity_id.txt（e09〜e11）を宣言。実装で判明した実際の書き込みを契約へ写した。0.4 は R3-01〜R3-03 の反映。0.3 で決めた方針に、前の版の記述が追随していなかった箇所の掃除。proposal_version は schema 版の const のため、文書の版はここで持つ"
    review_status: "page-rereviewed-2026-09-05: とんすけが改訂版の正本ページを再レビューし承認。decision(held)→nominated で page identity を再束縛済み（validate OK）。candidate は nominated。accepted は未"
skill_slug: monthly-listing-recon-csv
distill_id: d-6ddce7f6
kind: brownfield
source_refs:
  - path: wiki/concepts/MonthlyListingReconCsv.md
    sha256: fe0b1eca140f9fdcf9fa7770fc2c9664df1b0c3d4a799401a723480ec79fd22a
    role: wiki-page
    excerpt: "目的は1つだけ——「eBayに出品されているのに OpenLister が知らない品」を見つける。/ 片方だけで突合してはいけない。全アカウント分が揃わないうちは「ギャップ0件」も「ギャップN件」も意味を持たない。/ Listing site が空の行は除外されずに US として残る。/ 突合（読むだけ）と取り込み（書く）が同じ入口に同居している。"
  - path: scheduled-tasks/monthly-listing-recon-csv/SKILL.md
    sha256: 5bd03a38682e11b368758dc9d1f57ea54eb26110537cc9f475e7aacc157c5e0d
    role: existing-skill
    excerpt: "月1のCSV出品突合（未監視ギャップ専用）。ActiveList APIでは測れない「eBayに出ているのにOLが知らない品」を探す / 1. とんすけに Seller Hub からアクティブ出品CSVのダウンロードを依頼する（なな側からは取得できません）"
  - path: eBay/OpenLister/docs/task-board.html
    sha256: c213f9352e49434f74081c7d36e98cd5ad91b425b8be6bdef0187fba04760b1c
    role: task-board
    excerpt: "✅8/23 決着: このカードは「3つの目的」から「1つ」に縮小した。③未監視ギャップ → 残す。ただし月1で十分。出品漏れは事故でなく取りこぼし＝緊急性は低いので月1。"
  - path: wiki/concepts/OpenListerReconciliationGhosts.md
    sha256: 4db002034d63fb97ad7d620e7283c37da92ea622711c1b54dd5e863498780e74
    role: other
    excerpt: "eBay活性・OL未監視（逆向き）= 監視漏れ = 売り越しリスクの本命 / 突合は必ず Listing site=US でフィルタしてから（実弾: 24,696行中18,044行が複製）"
  - path: eBay/OpenLister/src/openlister/migration.py
    sha256: ebdc40f9c1a2c947bf674df4e780a266129f60fe5dd91cf786805cdf67d61c33
    role: runtime
    excerpt: "def load_ebay_active_csv(path: str, site: str = \"US\") -> dict[str, dict]: / site: 対象サイト（既定US）。AU/UK/DE等はeBaymagの複製でUS本体に連動するため突合・監視の対象から外す"
  - path: eBay/OpenLister/src/openlister/cli.py
    sha256: d005b5963bee022924ee058afb38df4eaaa934f513b12564c0d8eecf614b0999
    role: runtime
    excerpt: "import-orphans <ebay_csv> <account_name> [--apply]: account_id で絞った Product.ebay_item_id を読み、CSV 側にしか無い ItemID を孤児として出す（既定 dry-run）。--apply は同じコマンドで Product を INSERT する"
extracted_requirements:
  procedure:
    - "月次 run の開始時に expected account set（manekineko-japan / luckystreakjapan）を固定する"
    - "とんすけへ、対象アカウント全部の Seller Hub アクティブ出品CSV の提供を依頼する"
    - "全アカウント分が揃うまで blocked(input_missing) として待つ（部分実行しない）"
    - "各 CSV を Listing site = US でフィルタし、US 本体の ItemID を抽出する"
    - "アカウントごとに、同一アカウントの products（read-only・account_id で絞る）と突合し、CSV にあって DB に無い ItemID を候補として抽出する"
    - "候補を1件ずつ、eBaymag 複製でないか・別アカウントでないかを確認し、confirmed / rejected / inconclusive と理由を記録する"
    - "確認結果つきで report に載せ、アカウント別の件数と根拠を添えて報告する"
  preconditions:
    - "expected account set の全 CSV が存在し、mtime が当月であること（1本でも前月以前なら precondition_failed）"
    - "expected account set に無いアカウントの CSV が混じっていないこと（provenance 不明の入力を黙って使わない）"
    - "全データ行の Listing site が非空であること（既存 load_ebay_active_csv は空値を US として通す fail-open があるため、その既定値に寄りかからない）"
    - "US 行の Item number が全て有効な数値であること（既存パーサは非数値行を黙って skip するため、skip 件数0を検証する）"
    - "account ごとの US 行数が0でないこと。Item number の重複を検出すること（黙って上書きしない）"
    - "report は exclusive create。既に存在する場合は既存 bytes を変えず precondition_failed"
    - "OpenLister DB へ read-only で接続でき、各アカウントの active 件数が取得できること（いずれかが0件なら precondition_failed）"
    - "DB 参照は書き込み能力を持たない入口だけを使うこと（import-orphans / import-elister は --apply で INSERT するため経由しない）"
    - "qty差・幽霊は自動化済みのため対象外（追うと ActiveList 上限由来の偽陽性に時間を溶かす）"
  inputs:
    - "Seller Hub のアクティブ出品CSV（アカウントごとに1本・人が提供・機密）"
    - "OpenLister products（read-only・account_id スコープ）"
  outputs:
    - "突合 report（アカウント別の未監視ギャップ候補・1件ごとの確認結果と理由つき）"
    - "とんすけへの結果通知（completed / blocked のいずれか1回。blocked では不足アカウントを名指しする）"
  failure_handling:
    - "CSV が1本でも未着: blocked(input_missing)。不足アカウントを名指しして次回 run で再依頼する"
    - "CSV は到着したが内容が要件を満たさない（空ファイル / 前月以前 / 必須列欠落 / 想定外アカウント label / 行単位検査の不成立）: blocked(precondition_failed)。未着（input_missing）と到着済み不正（precondition_failed）を混ぜない"
    - "いずれかのアカウントの active 件数が0: precondition_failed（突合結果0件と区別する）"
    - "確認が inconclusive のまま残る候補がある: blocked(precondition_failed)＋reason に verification_incomplete。0件と報告しない（現行 event schema の block_kind は5値で閉じているため既存語彙へ写す）"
    - "候補が数千件単位で出た場合: eBaymag 複製の混入を最初に疑い、Listing site フィルタを再確認する"
effect_contract:
  path: distill/monthly-listing-recon-csv/effect-contract.json
  sha256: dc25c2125065f4715b380274e675b85b0e2a7b85f849fecbe0c0fddab5ef51f1
hosts:
  - host: claude
    entry: scheduled-task-skill
    canonical_deploy_path: scheduled-tasks/monthly-listing-recon-csv/SKILL.md
    trigger:
      task_id: monthly-listing-recon-csv
      schedule: "0 10 23 * *"
      timezone: Asia/Tokyo
      live_snapshot_sha256: 5bd03a38682e11b368758dc9d1f57ea54eb26110537cc9f475e7aacc157c5e0d
      status: snapshot
bundle_scope:
  - claude/SKILL.md
  - claude/recon_gap.py
sensitive_inputs:
  - name: Seller Hub アクティブ出品CSV（アカウントごとに1本）
    sensitivity: high
    retention: completed なら report と evidence の固定直後に同一 run 内で削除（effect e06）。blocked のまま入力が残った場合は30日経過後の最初の prepare で削除。undeploy / disable 時は e08 が全 run を掃き出す
    redaction: evidence には account・行数・列名・sha256・取得日のみ。タイトル・価格・数量は残さない
    identifiers: ItemID は evidence では hash 化して保持（report 本文には平文で残るが tracked evidence には出さない）
review:
  distill_reviewed_by: とんすけ（Tomoyuki Yagi）
  distill_reviewed_at: "2026-09-05"
---

# proposal（fixture）

> 実物の共有 Vault（`.wiki`）の proposal frontmatter を **そのまま複製**した fixture。
> 本文は落としてある。人が書くブロック形式 YAML を parser が読めることを固定するために置く。

