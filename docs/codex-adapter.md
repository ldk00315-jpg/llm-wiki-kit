# Codex SessionStartアダプター

`adapters/codex/session_start.py` は、Coreの `build_index_context()` が返す索引を
Codexの `SessionStart` フック形式へ包む薄いアダプターです。索引の生成・安全化・
delimiter・8,000文字上限はCoreだけが担当し、このファイルは変更しません。

## 配線

1. `adapters/codex/hooks.example.json` を、利用する設定レイヤーの `hooks.json` へ
   コピーします。個人共通なら `~/.codex/hooks.json`、プロジェクト限定なら
   `<プロジェクト>/.codex/hooks.json` です。
2. `command` と `commandWindows` のスクリプトのパスを、実在する**絶対パス**へ
   変更します。WindowsではPython 3.10以上の `python` がPATH上で解決できることも
   確認してください。必要なら `python` を `py -3` へ変更できます。Codexは
   セッションのcwdでコマンドを実行するため、相対パスはサブディレクトリからの
   起動で壊れます。
3. Codexを起動し、`/hooks` で定義を確認して信頼します。プロジェクトローカルの
   `.codex/` 自体も信頼済みである必要があります。定義が変わると再確認が必要です。

Windowsでは `commandWindows` が使われます。Codexはこれを `cmd.exe /C` で解決する
ため、PowerShell構文は使っていません。

`additionalContextLimit: 0` はCodex側のspillを無効にして完全な
`additionalContext` を渡します。この設定は、Coreが出力を最大8,000文字へ制限する
ことを前提にしています。アダプター側で再度切り詰めないでください。

公式仕様: [Codex Hooks](https://developers.openai.com/codex/hooks)

## 動作確認

Wikiページが存在するワークスペースで、次のように直接実行できます。

```powershell
$env:LLM_WIKI_ROOT = "C:\path\to\workspace\.wiki"
'{"source":"startup"}' | python adapters\codex\session_start.py
```

標準出力は1個のJSONオブジェクトだけです。`source` が `compact` の場合、Coreの
コンパクション回復ブロックが同じdelimiter内に含まれます。Wikiや対象ページが
無い場合は何も出力しません。失敗時もセッションを止めず、可能なら
`.wiki/diagnostics/hooks.log` に診断を追記します。
