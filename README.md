# llm-wiki-kit — エージェント用LLM Wiki スターターキット

AIコーディングエージェント（Claude Code等）に「自分専用のWiki」を持たせ、
セッションを跨いで知識を複利で積み上げるためのスターターキット。

Andrej Karpathy が提唱した LLM Wiki のアイデア（raw は不変・synthesis をLLMが
所有し更新し続ける）の実装に、実運用から生まれた **呼び出しインデックス** を
足したものです。

## 何が嬉しいのか

ふつうのメモやドキュメントは「エージェントが読みに行かない」から腐ります。
このキットは3つの仕掛けでそれを解決します:

1. **呼び出しインデックス（SessionStartフック）** — セッション開始時に
   「全ページのタイトル＋1行要約」だけを自動でAIの視界に注入。本文は関連する
   作業のときにAIが自分で開く。軽いのに、道具が「感覚」に変わる
2. **運用契約（スキーマ）** — 何を記録し、何を記録しないか。日常作業の中で
   エージェントが自発的にWikiを育てるルールまで含む
3. **コンパクション境界の検知と回復リマインダー（＋1行ジャーナル）** —
   長いセッションの要約（コンパクション）は「何をしたか」を残し「なぜ・細部・
   失敗過程」＝知見の肉を削ります。しかも知見が一番溜まった瞬間と消える直前は
   同じタイミング。このキットは公式にサポートされた経路だけで守ります:
   要約**直前**にPreCompactフックが `.wiki/inbox/journal.md` へ境界マーカーを
   追記し（ディスクなので要約で消えない）、要約**直後**のセッション再開
   （`SessionStart` の `source: "compact"`）で索引フックが「journalを確認し
   未記録知見を書き出せ」という回復指示を注入します。
   **注意: フックが永続化するのは境界の事実であって、知見そのものではありません。**
   知見の耐久性は、日常の即時書き出しと1行WAL（スキーマのAuto-capture節）が
   本体で、フックはその取りこぼしを拾う安全網です

## 動作要件

- **Python 3.10+** — Coreとフック／アダプターのランタイム（外部パッケージ依存はなし）
- **PowerShell** — メンテCLI用。Windows標準の Windows PowerShell 5.1 で動きます
  （`powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...` で実行）。
  PowerShell 7 (`pwsh`) があればそのまま `pwsh -File ...` でも可。
  **`pwsh` はWindowsに標準搭載されていない**別インストール品なので、
  以下の例は 5.1 で動く形で書きます

## 5分セットアップ（Claude Code）

### 1. Wikiの雛形とCLIを配置

`template/.wiki/`・`scripts/`・`core/llmwiki.py` をワークスペースへコピーして初期化:

```powershell
Copy-Item -Recurse template\.wiki <あなたのワークスペース>\.wiki
Copy-Item -Recurse scripts <あなたのワークスペース>\scripts
Copy-Item core\llmwiki.py <あなたのワークスペース>\scripts\llmwiki.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <あなたのワークスペース>\scripts\llm-wiki.ps1 init -WikiRoot <あなたのワークスペース>\.wiki
```

**v1.3系からCLIの正本はPython**（`core/llmwiki.py`・依存ゼロ）です
（旧配置の削除を検討するのは将来のv2。それまでは互換wrapperを維持します）。
`llm-wiki.ps1` は従来の呼び出し形を維持する互換wrapperで、同じディレクトリの
`llmwiki.py`（無ければキット内 `../core/llmwiki.py`）へ委譲します。
Pythonから直接呼ぶ場合:

```powershell
python <あなたのワークスペース>\scripts\llmwiki.py lint --wiki-root <あなたのワークスペース>\.wiki
```

書き込みコマンド（init / ingest / reindex）はVault単位の協調lock
（`.wiki/.lock/`）で直列化され、すべての書き込みはatomic
（一時ファイル→置換）です。外部編集と衝突した場合は上書きせず
`*.conflict-<時刻>-<id>` ファイルを生成します。

lockは**fail-closed設計**です: 他のwriterのlockを自動で奪いません。
プロセス異常終了でlockが残った場合は、owner情報を確認の上で明示的に
解除してください:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\llm-wiki.ps1 unlock -WikiRoot <ワークスペース>\.wiki
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\llm-wiki.ps1 unlock -WikiRoot <ワークスペース>\.wiki -Force
```

（1回目はowner情報の表示のみ・`-Force` 付きで実際に解除）

### 2. フックを配線

`hooks/` の2ファイルを好きな場所に置き、`~/.claude/settings.json` に:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["<置いた場所>/wiki_index_hook.py"],
            "timeout": 10
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["<置いた場所>/precompact_hook.py"],
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

- **wiki_index_hook.py（SessionStart）** — セッション開始時に索引を注入。
  出力は約8,000文字で自動的に打ち切り、省略時は「残りN件・全索引のパス」を
  明示します（フック出力は公式に10,000文字で切り詰められるため、その内側で
  自分の意思で予算管理する）。`source: "compact"` での再開時は、索引の前に
  コンパクション回復指示を注入します
- **precompact_hook.py（PreCompact）** — コンパクション直前に
  `.wiki/inbox/journal.md` へ境界マーカー（時刻・トリガー種別）を追記します。
  境界の**事実**をディスクに残すもので、知見の本文を保存するものではありません。
  transcriptの絶対パスは意図的に書きません（OSユーザー名等の漏えい面になるため）

どちらもカレントディレクトリから上へ辿って最初の `.wiki/` を自動発見します
（gitと同じ流儀・環境変数 `LLM_WIKI_ROOT` で固定も可）。`.wiki/` が見つからない
場所では黙って何もしません。フック内部で失敗した場合は
`.wiki/diagnostics/hooks.log` に記録を試みます（セッションは壊しません）。

### 3. スラッシュコマンドを配置

`commands/*.md` を `~/.claude/commands/`（全プロジェクト共通）か
`<プロジェクト>/.claude/commands/`（プロジェクト限定）へコピー:

| コマンド | 役割 |
|---|---|
| `/wiki-ingest <ソース>` | URL・ファイル・テキストを取り込んで統合ページ化 |
| `/wiki-query <質問>` | Wikiから引用付きで回答（無ければ無いと言う） |
| `/wiki-health` | 構造チェック（トークン消費なし・毎日OK） |
| `/wiki-lint` | 内容の品質チェック（矛盾・リンク切れ・陳腐化） |
| `/wiki-graph` | ページ関係のグラフをHTML出力 |
| `/wiki-overview` | 全体俯瞰ページを再生成 |

### 4. 動作確認

新しいセッションを開くと、冒頭にこう出ます:

```
[LLM Wiki索引（自動注入・SessionStart）] 過去の判断・罠・パターンの目録。…
- LLM Wiki Pattern — A local-first knowledge workflow where agents compile...
```

### 5. 自動captureを有効にする（重要）

索引フックだけでは「読む」側しか繋がりません。「書く」側＝日常作業の中で
エージェントが自発的にWikiを育てる動きは、CLAUDE.md（プロジェクトの
`CLAUDE.md` またはワークスペース共通のもの）に次のブロックを貼ることで
有効になります:

```markdown
## 作業内容の記録（LLM Wiki）
作業の中で「再利用される判断・発見・パターン・回避策」が生まれたら、
自発的に `.wiki/` に記録する。手順とトリガー基準は
`.wiki/schema/AGENTS.llm-wiki.md` の「Auto-capture during everyday work」を参照。
迷うもの・手が離せないときは `.wiki/inbox/journal.md` に1行だけ落とす（WAL）。
```

これを貼らない場合、Wikiは `/wiki-ingest` の明示実行でしか育ちません。

## Codexで使う

Codex用の `SessionStart` アダプターと `hooks.json` の設定例を同梱しています。
絶対パスの設定、Windowsの `commandWindows`、フックの信頼レビューを含む手順は
[`docs/codex-adapter.md`](docs/codex-adapter.md) を参照してください。Coreが生成する
索引とcompact回復ブロックはClaude Code版と同一で、Codex側では
`hookSpecificOutput.additionalContext` へJSON包装して注入します。

## Obsidianで見る（任意・おまけ）

Wikiの実体はただのMarkdownフォルダなので、[Obsidian](https://obsidian.md/) で
「フォルダをVaultとして開く」だけで人間用のビューアになります。テンプレには
設定済みの `.obsidian/` を同梱してあり、開いた瞬間から：

- **グラフビュー**（`Ctrl+G`）が色分け済み — 🔴 syntheses（蒸留・地図）／
  🟢 concepts（個別知見）／🟠 overview（入口）。`raw/`・索引・ログは
  初期フィルタで非表示＝**知識のネットワークだけ**が見える
- **ブックマーク**にサンプルページ・WALジャーナル・スキーマを登録済み
- `[[wikilink]]` 記法を維持（Markdownリンクへ自動変換しない）・
  ページのリネーム時はリンクが自動追従・新規ノートは `inbox/` に落ちる

グラフの読み方のコツ:

| 見えるもの | 意味 |
|---|---|
| 大きいノード | リンクが多い＝ハブ。育つと syntheses（地図ページ）がここに来るのが健全 |
| 薄い灰色のノード | **リンク先が存在しない壊れた `[[wikilink]]`**。掃除リストが目で見える |
| どこにも繋がらない孤立ノード | どの地図からも参照されていないページ。統合か削除の候補 |

**Obsidianはあくまで人間側の観測装置で、実行時依存ではありません。**
エージェント側の読み書き・フック・CLIはObsidian無しで完結します（入れなくても
何も欠けない）。Dataview等のプラグイン固有記法を本文に書き込むとエージェントには
ノイズになるので、使う場合も索引系ページに閉じるのがおすすめです。

## 構成

```
core/llmwiki.py              メンテCLIの正本（Python・lock/atomic write/F-06内蔵）
scripts/llm-wiki.ps1         互換wrapper（従来の呼び出し形→Python coreへ委譲）
hooks/wiki_index_hook.py     呼び出しインデックス＋compact回復注入（SessionStartフック）
hooks/precompact_hook.py     境界マーカーのディスク永続化（PreCompactフック）
adapters/codex/              Codex SessionStartアダプター＋hooks.json設定例
commands/wiki-*.md           Claude Code スラッシュコマンド6本
tests/smoke.ps1              挙動パリティ回帰（28 assertion・wrapper経由でcoreを検証）
tests/test_core.py           coreユニットテスト（lock/atomic/F-06/往復）
tests/test_codex_adapter.py  CodexのJSON/BOM/compact/設定契約テスト
docs/cross-agent-design.md   クロスエージェント設計書（Claude×Codex合意済み）
template/.wiki/              Wikiの雛形（スキーマ＋WALジャーナル＋サンプルページ＋raw実体入り）
template/.wiki/.obsidian/    Obsidian用の設定済みVault構成（任意・無くても動く）
```

## テスト

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\smoke.ps1
```

fresh init・lint・YAMLエスケープ・日本語slug・read-only性・created保存など
14項目の回帰テスト。一時ディレクトリだけを操作します。

## Windowsの注意

- フックは標準出力をUTF-8に明示設定済み（既定のCP932だと日本語が化けるため）
- `llm-wiki.ps1` が生成するMarkdownはBOM無しUTF-8です（PowerShell 5.1の既定
  エンコーディング問題を回避）。`.ps1` スクリプト自体はBOM付きUTF-8です
  （5.1はBOM無しUTF-8スクリプトをCP932として誤読しパースエラーになるため）

## 既知の制限（v1.2.1時点・設計対応はロードマップ）

- **並行書き込み**: ingest/reindex/logにロックや原子的renameはありません。
  複数エージェントの同時書き込みでは索引の取りこぼしが起こり得ます。
  同時実行する場合はingestを直列化してください
- **注入内容の信頼**: 索引フックはページのsummaryを無検証で注入します。
  外部ソースをingestする場合、悪意ある文書の命令文がsummaryへ混入すると
  毎セッション再注入される経路になり得ます。**信頼できないソースの要約は
  人間が一読してから確定する**運用を推奨します
- **秘密情報**: raw取り込みはURLのquery stringやローカル絶対パスをそのまま
  保存します。`.wiki/` をGit管理する場合は、トークン付きURL等が入り込んで
  いないか確認してください（`raw/` と `log.md` を `.gitignore` する選択肢も
  あります）
- **URL ingest**: 本文抽出なしの素朴なHTML保存です。大きなページや
  スクリプトだらけのページはLLMコストを増やします。重要なソースは
  本文だけをテキストで渡すのが確実です

## クレジット

- LLM Wiki の原アイデア: [Andrej Karpathy](https://x.com/karpathy)
- 呼び出しインデックス・スキーマ・運用ルールは実運用（eBay輸出業の運用OS開発）で
  磨いたものです

## License

MIT
