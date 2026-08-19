# Codexアダプター

`adapters/codex/session_start.py` は、Coreの `build_index_context()` が返す索引を
Codexの `SessionStart` フック形式へ包む薄いアダプターです。索引の生成・安全化・
delimiter・8,000文字上限はCoreだけが担当し、このファイルは変更しません。

`adapters/codex/pre_compact.py` は、Codexの `PreCompact` をCoreの
`append_compact_boundary_marker()` へ渡し、要約直前の境界をjournalへ永続化します。
マーカー文言・lock・atomic write・競合検知はCoreだけが担当します。

## 配線

1. `adapters/codex/hooks.example.json` を、利用する設定レイヤーの `hooks.json` へ
   コピーします。個人共通なら `~/.codex/hooks.json`、プロジェクト限定なら
   `<プロジェクト>/.codex/hooks.json` です。
2. `command` と `commandWindows` のスクリプトのパスを、実在する**絶対パス**へ
   変更します。WindowsではPython 3.10以上の `python` がPATH上で解決できることも
   確認してください。必要なら `python` を `py -3` へ変更できます。Codexは
   セッションのcwdでコマンドを実行するため、相対パスはサブディレクトリからの
   起動で壊れます。
3. Codexを**完全に終了してから起動**し、フックのレビューを承認します（`/hooks` でも
   定義を確認できます）。プロジェクトローカルの `.codex/` 自体も信頼済みである
   必要があります。定義が変わると再確認が必要です。

> **重要: 信頼するまでフックは実行されません（エラーも出ません）。**
> Codexは `hooks.json` の**エントリ単位**で信頼ハッシュを `config.toml` の
> `[hooks.state]` に保存し、未信頼・変更後未再信頼のエントリを黙ってスキップします
> （UIの文言も `Continue without trusting (hooks won't run)`）。
> 信頼がエントリ単位であるため、**同じファイル内の既存フックは動き続けます**。
> 「他のフックは効いているのに索引だけ来ない」という部分故障に見え、
> スクリプト側を疑いやすいので注意してください。
>
> また、**すでに開いているセッションに `SessionStart` は遡って発火しません**。
> `matcher` に `resume` を含めていても途中では再実行されないため、
> 新しいセッションを開くかアプリを再起動して確認してください。

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

SessionStartの標準出力は1個のJSONオブジェクトだけです。`source` が `compact` の場合、Coreの
コンパクション回復ブロックが同じdelimiter内に含まれます。Wikiや対象ページが
無い場合は何も出力しません。失敗時もセッションを止めず、可能なら
`.wiki/diagnostics/hooks.log` に診断を追記します。

PreCompactはstdoutへ何も出さず、`trigger`（`manual` / `auto`）を付けた境界を
`.wiki/inbox/journal.md` へ追記します。transcriptの絶対パスは保存しません。

## 索引が注入されないときの切り分け

アダプター単体が正しくても、ホストが呼ばなければ索引は届きません。次の順で
**機械的に**確認してください（エージェントの自己申告は証拠になりません）。

1. **信頼を確認する。** `config.toml` の `[hooks.state]` に
   `…hooks.json:session_start:0:0` のエントリがあるか。無ければ上記の信頼ゲートです。
2. **モデルの視界を直接見る。** `codex debug prompt-input` がモデルへ渡される
   プロンプト入力をJSONで出力します。ここに `<<<LLM_WIKI_CONTEXT>>>` が
   無ければ、注入されていないことが確定します。
3. **書式の互換を確認する。** Codexの版が上がった直後なら、
   `rg -a "SessionStart|PreCompact|hookSpecificOutput|additionalContextLimit" <codex実行ファイル>`
   で識別子が残っているかを見ると、契約変更かどうかを数秒で切り分けられます。
4. **Vaultが見つかるか確認する。** アダプターはcwdから上へ `.wiki/wiki` を探します。
   見つからない場合は**意図的に無出力**です（他プロジェクトでの誤発火を防ぐため）。
   `LLM_WIKI_ROOT` で明示指定もできます。

`trusted_hash` を手で計算して `config.toml` へ書き込まないでください。アプリが
所有する状態ファイルの手書きは、正規の管理を隠して後から破綻します。
同じ理由で `--dangerously-bypass-hook-trust` も使わないでください。
