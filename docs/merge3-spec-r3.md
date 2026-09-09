# merge 3 仕様 R3: 攻撃側レビュー（`tests/redteam_merge3_probes.py`・24 failed）への修正

`docs/merge3-spec.md` → `-r2.md` の続き。作業ツリー（R1＋R2・295 passed）の上に積む。禁止事項・作法は同じ。
**受け入れ条件: 下の各項目に対応する probe を書き換えずに赤→緑にする。** probe の期待が過剰なら報告に書く（直さない）。
**終了条件（とんすけ合意）: 次の攻撃側再確認で P1 が 0 なら main へ。P2 は台帳へ。**

## P1

### R3-1 `bound_to` の束縛先は自分の proposal / effect contract でなければならない（probe A1/A2）

`check_bound_drift` は path→hash の一致だけ見ている。修正: accepted の `bound_to.proposal.path` は
`find_proposal(distill_id)` が返す rel path と**等しい**こと、`bound_to.effect_contract.path` は `distill/<slug>/effect-contract.json` と
**等しい**ことを要求。違えば「bound_to points outside its own candidate」で FAIL。等値は正規化した portable path 同士（下 R3-2 の正規化を通す）。

### R3-2 path alias（大小文字・末尾ドット/空白）で owner 検査を迂回できる（probe A3/A4）

`PORTABLE_PATH_RE` が末尾ドット/空白を許し、owners lookup は生文字列。修正:
- `PORTABLE_PATH_RE` で**各セグメントの末尾ドット・末尾空白・先頭空白を禁止**（Windows の alias）
- owner / 束縛先の比較は `normalize_portable(path)`（`os.path.normcase` 相当の**大小文字畳み込みは Windows でのみ**・区切りは `/`）を
  両側に掛けてから行う。resolve 先が同じ file でも、正規化した文字列が一致しなければ拒否（「同じ file に着地する」を根拠に通さない）

### R3-3 candidate identity（`page_path`）の連続性（probe B1/B2）

chain は `distill_id` だけで繋がり、`subject.page_path` の連続性を見ない。修正:
- `state_chain` の各 state event で、`subject.page_path` が**直前の state event と同じ**であることを検査。違えば
  「subject identity changed」で FAIL（page の移動・改名は現状の契約に無い。将来必要なら専用 event を設計する——今回は作らない）
- `cmd_nominate` は、同じ `distill_id` を持つ**別の page** からの nominate（held からの再 nominate を含む）を拒否する
  （「distill_id は Vault 内 unique・review 済み page identity にしか state event を出せない」契約 §2）
- 同一秒の並びに依存しないこと（probe B2 の「乱数依存」を消す）

### R3-4 head event の in-place 書き換え（probe A5）— 契約で「保証しない」と明記し、検出できる範囲を広げる

head event は誰の `previous_event_sha256` にも参照されない＝錨が無い。**append-only store の構造的限界**なので、
完全な検出は保証しない（契約 §10 に「head event の改変は次の event が書かれるまで検出されない」と明記）。
ただし次だけ入れる: `distill/_index.md` に **head event の event_id と sha256** を含め、`validate` は index の head sha と実体を照合する
（index が古ければ既存の「reindex してください」で拾える）。これで「改変後に reindex を忘れた」ケースは検出される。

## P2

### R3-5 unparseable な proposal で `decide accepted` が通る（probe C1）

`find_proposal` は flat 抽出だけで record 化している。修正: `compute_bound_to` は **subset parser で読めた proposal だけ**を束縛対象にし、
unparseable なら DistillError で何も書かない（仕様 A「frontmatter が壊れている → 何も書かない」）。

### R3-6 plain scalar のアポストロフィで全体が unparseable（probe C2）— **最優先の P2**

`note: it's fine` が「クォート未閉」扱い。修正: クォート判定は **値の先頭文字が `'` または `"` のときだけ**。それ以外の `'` `"` は
plain scalar の一部。コメント除去も同じ規則（クォートで始まらない値では `#` の前に空白があるときだけコメント）。
実物の proposal の excerpt（`"…（既定dry-run）。--apply は…"` のような二重引用符で始まる値）と、`it's` を含む plain 値の両方を fixture に。

### R3-7 64桁すべて数字の sha256 が int（probe C4）

修正: `sha256` という key の値、および `check_refs` / `bound_to` で hash として読む値は**常に文字列として扱う**（int で来たら
`str(v).zfill(64)` はしない——桁落ちを復元できないので、int なら `unverifiable` ではなく **mismatch（形式不正）で FAIL**）。
parser 側では「64桁の数字だけの plain scalar は文字列のまま」に規則を足す（日付を文字列のままにするのと同じ扱い）。

### R3-8 `compute_bound_to` が proposal を2回読む（probe C3）

修正: proposal は**1回だけ**読み、その bytes から frontmatter 解析と hash の両方を行う（`scan_proposals` に bytes を渡す）。
docstring の主張に実装を合わせる。

### R3-9 新しい bound_to 無し accepted も legacy 扱い（probe A6）

修正: legacy と認めるのは **merge 3 導入前**の event だけ。判定は `occurred_at < MERGE3_CUTOFF`（定数 `"2026-09-09T12:00:00Z"`。
契約に明記）。cutoff 以降の accepted で `bound_to` が無ければ FAIL。

### R3-10 rereview で `candidate_bundle` が黙って消える（probe B3）

修正: `rereview` は `--bundle-sha256` が無ければ**直前の accepted の `candidate_bundle` を引き継ぐ**。明示的に外すには
`--drop-bundle` を要する（引数は増えるが、黙って消えるよりよい）。

### R3-11 D の食い違い検査が型に壊される（probe D1/D2/D3/D3b）

修正: `document_revision` は**文字列として**比較（parser で `^\d+\.\d+$` の plain は文字列のまま。`0.1` と `0.10` は別）。
`extensions.revision` が mapping なら `.document_revision` を比較対象にする（実物の形）。理由文に dict をそのまま載せない。

### R3-12 1行 JSON 経路が parser の約束を破る（probe E1/E2）

修正: `json.loads` は `object_pairs_hook` で **key 重複を検出して unparseable**、`parse_constant` で NaN/Infinity を拒否。
入れ子 JSON は許す（YAML flow の部分集合として妥当）が、深さ上限（8）を置く。

### R3-13 unparseable 時の identity fallback（probe E5）

修正: unparseable な proposal は record 化しない（R3-5 と同じ）。flat 抽出の「最後の同名 key」に依存する経路を消す。

## P3

### R3-14 ReDoS（probe E3）

`_YAML_PLAIN_KEY_RE` の `[^:]+?\s*:` を、`:` の**直後が空白か行末**である最初の `:` を線形走査で見つける実装に置き換える（正規表現を使わない）。
1MB の1行で 100ms 以内を目安にテスト。

### R3-15 数字だけの slug directory（probe C5）

`skill_slug` / `distill_id` / `slug` として読む値は文字列として扱う（R3-7 と同じ規則の適用先を明示）。

### R3-16 bidi / 行区切り文字の素通り（probe F1/F2）

`_safe` で **U+202A〜U+202E・U+2066〜U+2069（bidi 制御）と U+0085・U+2028・U+2029** を `\uXXXX` へ逃がす。
`_index.md` へ書く page_path も同じ関数を通す。

## テストの縛り

- 各 R3 項目の probe（`tests/redteam_merge3_probes.py`）が**書き換えずに**緑になる
- R3-1〜3 は「旧（R2）実装で赤」を実測
- 既存 295 件を消さない。期待値を変えるなら理由をコメントに
- 最後に実物の Vault（読むだけ）へ `validate --refs` を当て、出力を報告に貼る（期待: R2 と同じ結論＝legacy 1・mismatch 1。
  **アポストロフィ修正で unparseable が出ないこと**）

## 報告様式

R1/R2 と同じ。加えて probe の最終結果（何件緑になったか・残った赤とその理由）。**commit・push はしない。**
