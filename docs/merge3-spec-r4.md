# merge 3 仕様 R4: 再確認で残った P1 4件（G1〜G4）だけを閉じる

`docs/merge3-spec-r3.md` の続き。作業ツリー（R1〜R3・312 passed・probe 31/32）の上に積む。禁止事項・作法は同じ。
**範囲はこの4件だけ。** 設計を広げない・引数を増やさない・良かれと思う追加をしない。
受け入れ: `tests/redteam_merge3_probes.py` の G1〜G4 を**書き換えずに**緑にする。A5・G10 は台帳（直さない）。

## R4-1 【P1】`normalize_portable` の畳み込みは ASCII A–Z だけ（probe G1・G2）

`str.lower()` は U+212A KELVIN SIGN を `k` に畳むが、NTFS は別名として扱う。束縛先の等値検査と `cmd_nominate` の一意性検査が
「同じ path」と誤認し、別 file を自分の proposal / 同じ page と見なす。
修正: 大小文字の畳み込みは `str.translate` で **ASCII の A–Z → a–z だけ**（非 ASCII は一切触らない）。Windows 以外では畳まない。
`os.path.normcase` を使わない（同じ穴を持つ）。仕様 R3-2 の「`normcase` 相当」という文言も契約から消す。

## R4-2 【P1】chain 上で `occurred_at` の非減少を要求（probe G3）

`MERGE3_CUTOFF` の判定材料 `occurred_at` は攻撃者が書ける。今日の nominated の後ろに cutoff より前の `occurred_at` を持つ accepted を
繋ぐと legacy に化けて gate が開く。
修正: `state_chain` の検査で、各 event の `occurred_at` が**直前の event 以上**であること、かつ `event_id` の時刻 prefix
（`YYYYMMDDTHHMMSSZ`）が `occurred_at` と**同じ秒**であることを要求。違えば「occurred_at goes backwards / event_id time mismatch」で FAIL。
non-state event（opportunity 等）も chain 上の位置で同じ検査。legacy 判定はこの検査を通った chain に対してだけ行う。

## R4-3 【P1】`candidate_states.page_path` は chain head から採る（probe G4）

`candidate_states` が「file 順で最後の event（非 state 含む）」から page_path を採るため、承認時 bytes のコピーを別 path に置き、
その path を subject にした opportunity event を1つ書くと page drift 検査がコピーへ逸れる。
修正: `candidate_states` の `page_path` / `page_sha256` は **state chain の head**（nominated / decision / rereviewed）から採る。
非 state event の `subject.page_path` は **chain の page_path と一致**を要求（違えば「subject identity changed」で FAIL・R3-3 と同じ理由文）。

## R4-4 契約への追記

`docs/distillation-contract.md` §7 に「chain の時刻単調性（occurred_at・event_id prefix）を validate が検査する」「非 state event の subject は
chain の page identity と一致すること」を追記。A5（head event の in-place 改変は次の event まで検出を保証しない）は R3 のまま。

## テスト

- G1〜G4 緑（書き換えなし）。R3 実装で赤を実測
- 既存 312 件を維持。時刻単調性の追加で既存 fixture が落ちるなら、fixture の時刻を直す（検査を緩めない）
- 実物 Vault（読むだけ）へ `validate --refs` を当て、R3 と同じ結論（legacy 1・mismatch 1・index 不一致）であることを貼る

## 報告様式

R3 と同じ。**commit・push はしない。**
