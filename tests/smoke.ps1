# smoke.ps1 — llm-wiki-kit 回帰テスト（Windows PowerShell 5.1 / PowerShell 7 両対応）
#
# 実行:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\smoke.ps1
#   pwsh -File tests\smoke.ps1
#
# 一時ディレクトリだけを操作し、リポジトリのファイルは変更しない。

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$cli = Join-Path $repo "scripts\llm-wiki.ps1"
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("llm-wiki-kit-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 8))

$script:passed = 0
$script:failed = 0

function Assert {
    param([bool]$Condition, [string]$Name)
    if ($Condition) {
        $script:passed++
        Write-Output "PASS: $Name"
    } else {
        $script:failed++
        Write-Output "FAIL: $Name"
    }
}

try {
    New-Item -ItemType Directory -Force $work | Out-Null
    Copy-Item -Recurse (Join-Path $repo "template\.wiki") (Join-Path $work ".wiki")
    $root = Join-Path $work ".wiki"

    # --- T1: fresh init が仕様上のファイルを全部作る ---
    & $cli -Command init -WikiRoot $root | Out-Null
    Assert (Test-Path (Join-Path $root "wiki\overview.md")) "init creates wiki/overview.md"
    Assert (Test-Path (Join-Path $root "_index.md")) "init creates _index.md"
    Assert (Test-Path (Join-Path $root "raw\_index.md")) "init creates raw/_index.md"
    Assert (Test-Path (Join-Path $root "wiki\_index.md")) "init creates wiki/_index.md"

    # --- T2: fresh Vault で lint が成功する ---
    $lintOut = & $cli -Command lint -WikiRoot $root
    Assert ($lintOut -match "OK") "lint passes on fresh vault"

    # --- T3: コロン入りタイトルが valid YAML（引用符付き）になる ---
    & $cli -Command ingest -WikiRoot $root -Text "body" -Title "bad: value" | Out-Null
    $colonRaw = Get-ChildItem (Join-Path $root "raw\*.md") | Where-Object { $_.Name -ne "_index.md" } | Sort-Object Name | Select-Object -Last 1
    $titleLine = (Get-Content $colonRaw.FullName -Encoding UTF8)[1]
    Assert ($titleLine -eq 'title: "bad: value"') "colon title is double-quoted ($titleLine)"

    # --- T4: 引用符・改行入りタイトルもエスケープされる ---
    & $cli -Command ingest -WikiRoot $root -Text "body" -Title "quote `"here`" and more" | Out-Null
    $quoteRaw = Get-ChildItem (Join-Path $root "raw\*.md") | Where-Object { $_.Name -like "*quote*" } | Select-Object -First 1
    $qLine = (Get-Content $quoteRaw.FullName -Encoding UTF8)[1]
    Assert ($qLine -match '^title: ".*\\".*"') "embedded quotes are escaped ($qLine)"

    # --- T5: 日本語タイトルが無情報 slug に退化しない ---
    & $cli -Command ingest -WikiRoot $root -Text "honbun" -Title "日本語タイトル" | Out-Null
    $jpRaw = Get-ChildItem (Join-Path $root "raw\*.md") | Where-Object { $_.Name -match "note-[0-9a-f]{6}" }
    Assert ($null -ne $jpRaw) "japanese title gets hash slug, not 'source'"
    $jpRaw2 = Get-ChildItem (Join-Path $root "raw\*.md") | Where-Object { $_.Name -match "-source(\-\d+)?\.md$" }
    Assert ($null -eq $jpRaw2) "no degenerate 'source.md' filename"

    # --- T6: lint が壊れた raw frontmatter を検出する ---
    $brokenPath = Join-Path $root "raw\2026-01-01-broken.md"
    Set-Content -Path $brokenPath -Value @("---", "title: broken: unquoted", "---", "", "body") -Encoding UTF8
    $lintOut2 = & $cli -Command lint -WikiRoot $root 2>&1
    $global:LASTEXITCODE = 0  # reset for later assertions
    Assert (($lintOut2 -join "`n") -match "Unquoted colon") "lint detects unquoted colon in raw title"
    Remove-Item $brokenPath -Force

    # --- T7: status は存在しない root を作らない（read-only） ---
    $ghost = Join-Path $work "ghost\.wiki"
    $statusOut = & $cli -Command status -WikiRoot $ghost 2>&1
    $global:LASTEXITCODE = 0
    Assert (-not (Test-Path (Join-Path $work "ghost"))) "status does not create missing root"
    Assert (($statusOut -join "`n") -match "ERROR") "status reports error for missing root"

    # --- T8: reindex が created: を保存する ---
    $before = (Get-Content (Join-Path $root "wiki\_index.md") -Encoding UTF8 | Select-String "^created:").Line
    & $cli -Command reindex -WikiRoot $root | Out-Null
    $after = (Get-Content (Join-Path $root "wiki\_index.md") -Encoding UTF8 | Select-String "^created:").Line
    Assert ($before -eq $after) "reindex preserves created: date"

    # --- T9: 角括弧入りタイトルが索引 Markdown を壊さない ---
    & $cli -Command ingest -WikiRoot $root -Text "body" -Title "has [brackets] inside" | Out-Null
    $indexContent = Get-Content (Join-Path $root "raw\_index.md") -Raw -Encoding UTF8
    Assert ($indexContent -match "\\\[brackets\\\]") "brackets escaped in index link label"
}
finally {
    if (Test-Path $work) { Remove-Item -Recurse -Force $work }
}

Write-Output ""
Write-Output "Result: $passed passed, $failed failed"
if ($failed -gt 0) { exit 1 }
