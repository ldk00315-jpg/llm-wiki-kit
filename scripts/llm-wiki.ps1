[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("init", "status", "ingest", "reindex", "lint")]
    [string]$Command = "status",

    [string]$WikiRoot = ".wiki",
    [string]$Source,
    [string]$Text,
    [string]$Title
)

# llm-wiki.ps1 — 互換wrapper（v2系）
#
# 設計書 docs/cross-agent-design.md 決定#2 により、CLIの正本は
# Python core（core/llmwiki.py）へ移行した。本スクリプトは従来の
# 呼び出し形（-Command / -WikiRoot / -Source / -Text / -Title）を
# そのままPython CLIへ委譲する互換レイヤーである。
# 出力メッセージ・終了コードはv1.2.x系と互換。

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Python実行ファイルの解決（python → py -3 の順）
$pythonExe = $null
$pyLauncherArgs = @()
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) {
    $pythonExe = $cmd.Source
} else {
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) {
        $pythonExe = $cmd.Source
        $pyLauncherArgs = @("-3")
    }
}
if (-not $pythonExe) {
    Write-Output "ERROR: Python 3.10+ not found. llm-wiki-kit v2 requires Python (it is already required for the hooks)."
    exit 1
}

# core/llmwiki.py の解決: 同居（配備先）→ ../core（kitリポジトリ内）の順
$candidates = @(
    (Join-Path $PSScriptRoot "llmwiki.py"),
    (Join-Path (Split-Path $PSScriptRoot -Parent) "core\llmwiki.py")
)
$corePath = $null
foreach ($c in $candidates) {
    if (Test-Path $c) { $corePath = $c; break }
}
if (-not $corePath) {
    Write-Output "ERROR: core module not found. Copy core/llmwiki.py next to this script (or keep the kit layout)."
    exit 1
}

# 値はコマンドラインでなく環境変数で渡す。PS 5.1のネイティブ引数渡しは
# 埋め込み引用符を正しくエスケープしない（quoting地獄の構造的回避）
$argv = @($pyLauncherArgs) + @($corePath, $Command)
$env:LLMWIKI_WIKI_ROOT = $WikiRoot
if ($Source) { $env:LLMWIKI_SOURCE = $Source } else { Remove-Item Env:LLMWIKI_SOURCE -ErrorAction SilentlyContinue }
if ($Text)   { $env:LLMWIKI_TEXT = $Text }     else { Remove-Item Env:LLMWIKI_TEXT -ErrorAction SilentlyContinue }
if ($Title)  { $env:LLMWIKI_TITLE = $Title }   else { Remove-Item Env:LLMWIKI_TITLE -ErrorAction SilentlyContinue }

$env:PYTHONIOENCODING = "utf-8"
# PS 5.1は外部プロセスstdoutを既定でCP932デコードする → UTF-8に切替
$prevEnc = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    & $pythonExe @argv
    exit $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $prevEnc
}
