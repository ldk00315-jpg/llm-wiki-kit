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
3. **コンパクション耐性（PreCompactフック＋1行ジャーナル）** — 長いセッションの
   要約（コンパクション）は「何をしたか」を残し「なぜ・細部・失敗過程」＝知見の
   肉を削ります。しかも知見が一番溜まった瞬間と消える直前は同じタイミング。
   要約直前に保全指示を自動注入し、迷った知見は `.wiki/inbox/journal.md` に
   1行だけ先行ログ（WAL）として落とす運用で、知見が要約を跨いで生き残ります

## 5分セットアップ（Claude Code）

### 1. Wikiの雛形を配置

`template/.wiki/` をワークスペースのルートにコピーして初期化:

```powershell
Copy-Item -Recurse template\.wiki <あなたのワークスペース>\.wiki
pwsh -File scripts\llm-wiki.ps1 init -WikiRoot <あなたのワークスペース>\.wiki
```

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

- **wiki_index_hook.py（SessionStart）** — セッション開始時に索引を注入
- **precompact_hook.py（PreCompact）** — コンパクション直前に「wiki級知見の詳細を
  要約に残せ／要約後の最初のターンで未記録分を書き出せ」の二段指示を注入。
  発火すると「📚 wiki級知見の保全指示を要約者へ注入」と表示されます

どちらもカレントディレクトリから上へ辿って最初の `.wiki/` を自動発見します
（gitと同じ流儀・環境変数 `LLM_WIKI_ROOT` で固定も可）。`.wiki/` が見つからない
場所では黙って何もしません。

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

あとはエージェントに任せます。スキーマ（`.wiki/schema/AGENTS.llm-wiki.md`）に
「日常作業の中でいつ・何を記録するか」の基準が書いてあるので、CLAUDE.md 等から
参照させると、明示的な指示なしでもWikiが育ち始めます。

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
hooks/wiki_index_hook.py     呼び出しインデックス（SessionStartフック・依存ゼロ）
hooks/precompact_hook.py     知見保全指示の注入（PreCompactフック・依存ゼロ）
scripts/llm-wiki.ps1         メンテCLI: init / status / ingest / reindex / lint
commands/wiki-*.md           Claude Code スラッシュコマンド6本
template/.wiki/              Wikiの雛形（スキーマ＋WALジャーナル＋サンプルページ入り）
template/.wiki/.obsidian/    Obsidian用の設定済みVault構成（任意・無くても動く）
```

## Windowsの注意

- フックは標準出力をUTF-8に明示設定済み（既定のCP932だと日本語が化けるため）
- `llm-wiki.ps1` はBOM無しUTF-8で書き込みます（PowerShell 5.1の既定エンコーディング
  問題を回避）

## クレジット

- LLM Wiki の原アイデア: [Andrej Karpathy](https://x.com/karpathy)
- 呼び出しインデックス・スキーマ・運用ルールは実運用（eBay輸出業の運用OS開発）で
  磨いたものです

## License

MIT
