# merge 3 攻撃側レビュー: 状態機械と hash 束縛を偽装で崩す

あなたの役割は**実装者ではなく攻撃者**。`core/distill.py` の event store（状態機械・hash 束縛・validate）を、
仕様（`docs/merge3-spec.md`・`docs/distillation-contract.md`）の外側から崩しにいく。直すのは別の担当。
**再現手順つきで穴を報告する**ことだけが仕事。

## 禁止

- `core/`・`schema/`・既存テスト・docs を**変更しない**
- `I:\Workspace\.wiki`（共有 Vault・実データ）に触れない。再現はすべて `tmp_path` の store で行う
- commit / push しない
- 再現コードは `tests/redteam_merge3_probes.py` に**新規**で置いてよい。**穴を示す assert が赤になるのが成果**（緑にする必要はない）
- 実行: `I:\Workspace\eBay\OpenLister\.venv\Scripts\python.exe -m pytest tests/redteam_merge3_probes.py -q`

## 狙う場所

1. **hash 束縛の偽装**: accepted の `bound_to` を手で書き換えた event file／proposal を差し替えてから `rereviewed` で束縛し直す／
   `bound_to` に別 candidate の hash を入れる／sha256 の大小文字・前後空白・64桁でない値
2. **状態機械**: `rereviewed` を absent・held・rejected から出す／`rereviewed` を2連続／`nominated → rereviewed → decision held → nominated`
   の順で page_sha256 がどこを指すか／`previous_event_id` を過去の event に向けた分岐（chain の枝分かれ）／同一秒の event 並び
3. **validate の穴**: proposal が無い・frontmatter が壊れている・`skill_slug` が別 candidate を指す／`--refs` の base が root の外を指す・
   symlink/junction 経由で root 内へ戻る・`..` を含む portable_path／`unverifiable` を `ok` と数える経路
4. **built-in と schema のずれ**: 新フィールド（`bound_to` / `rereviewed` / `document_revision` / `expected_inputs` / `capability_boundary`）で、
   片方だけが通す JSON 値（null・bool・数値・list・入れ子）を再帰生成して総当たり
5. **書き込みの順序**: `decide accepted` が hash 計算に失敗したとき何も書かれないか／`rereview` が page 読み取り失敗で何も書かないか／
   lock の外で store を読む経路が残っていないか
6. **stdout / 理由文への漏れ**: 入力由来の任意文字列（frontmatter の値・path）が理由にそのまま載る経路

## 報告様式

穴ごとに: 番号・深刻度（P1 = 偽装した hash で gate が開く or 状態が黙って壊れる／P2 = 検出の欠落／P3 = その他）・再現（pytest 形式）・
反する契約（仕様書の箇所）。直し方は書かなくてよい（書くなら1行）。**崩せなかった観点も1行ずつ**。
最後に「この merge を main に入れてよいか」の意見を1段落。
