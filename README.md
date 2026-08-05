# llm-wiki-kit — エージェント用LLM Wiki スターターキット

AIコーディングエージェント（Claude Code等）に「自分専用のWiki」を持たせ、
セッションを跨いで知識を複利で積み上げるためのスターターキット。

Andrej Karpathy が提唱した LLM Wiki のアイデア（raw は不変・synthesis をLLMが
所有し更新し続ける）の実装に、実運用から生まれた **呼び出しインデックス** を
足したものです。

## 何が嬉しいのか

ふつうのメモやドキュメントは「エージェントが読みに行かない」から腐ります。
このキットは2つの仕掛けでそれを解決します:

1. **呼び出しインデックス（SessionStartフック）** — セッション開始時に
   「全ページのタイトル＋1行要約」だけを自動でAIの視界に注入。本文は関連する
   作業のときにAIが自分で開く。軽いのに、道具が「感覚」に変わる
2. **運用契約（スキーマ）** — 何を記録し、何を記録しないか。日常作業の中で
   エージェントが自発的にWikiを育てるルールまで含む

## 5分セットアップ（Claude Code）

### 1. Wikiの雛形を配置

`template/.wiki/` をワークスペースのルートにコピーして初期化:

```powershell
Copy-Item -Recurse template\.wiki <あなたのワークスペース>\.wiki
pwsh -File scripts\llm-wiki.ps1 init -WikiRoot <あなたのワークスペース>\.wiki
```

### 2. 呼び出しインデックスを配線

`hooks/wiki_index_hook.py` を好きな場所に置き、`~/.claude/settings.json` に:

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
    ]
  }
}
```

フックはカレントディレクトリから上へ辿って最初の `.wiki/` を自動発見します
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

## 構成

```
hooks/wiki_index_hook.py     呼び出しインデックス（SessionStartフック・依存ゼロ）
scripts/llm-wiki.ps1         メンテCLI: init / status / ingest / reindex / lint
commands/wiki-*.md           Claude Code スラッシュコマンド6本
template/.wiki/              Wikiの雛形（スキーマ＋サンプルページ入り）
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
