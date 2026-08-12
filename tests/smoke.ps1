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

function Invoke-PyHook {
    param([string]$PyExe, [string]$Script, [string]$Json, [string]$Cwd)

    # PowerShellパイプの暗黙エンコーディング（$OutputEncoding / BOM付与）に
    # 依存しないよう、Processでstdinへ生UTF-8バイトを直接書き込む
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PyExe
    $psi.Arguments = '"' + $Script + '"'
    $psi.WorkingDirectory = $Cwd
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $p = [System.Diagnostics.Process]::Start($psi)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)
    $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $p.StandardInput.Close()
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    if ($err) { Write-Output "HOOK-STDERR: $err" }
    $script:LastHookExit = $p.ExitCode
    return $out
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
    Assert (($lintOut2 -join "`n") -match "Unquoted 'colon\+space'") "lint detects unquoted colon+space in raw title"
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

    # --- T10: 引用符タイトルの ingest→reindex 往復で索引リンクが壊れない (R-02) ---
    # T4のタイトル "quote `"here`" and more" は既にingest済み。reindex後の索引を検証
    & $cli -Command reindex -WikiRoot $root | Out-Null
    $indexContent = Get-Content (Join-Path $root "raw\_index.md") -Raw -Encoding UTF8
    Assert ($indexContent -match '\[quote "here" and more\]\(') "quoted title round-trips to clean index label"
    Assert ($indexContent -notmatch '\\"here\\"') "no leaked YAML escapes in index"

    # --- T11: lint が閉じていない frontmatter を検出する (R-04) ---
    $unterm = Join-Path $root "raw\2026-01-02-unterminated.md"
    Set-Content -Path $unterm -Value @("---", 'title: "Missing closing"', "", "body without closing delimiter") -Encoding UTF8
    $lintOut3 = & $cli -Command lint -WikiRoot $root 2>&1
    $global:LASTEXITCODE = 0
    Assert (($lintOut3 -join "`n") -match "Unterminated frontmatter") "lint detects unterminated frontmatter"
    Remove-Item $unterm -Force

    # --- T12: lint が方言外エスケープを検出する (R-04/V-04) ---
    $badesc = Join-Path $root "raw\2026-01-03-badescape.md"
    Set-Content -Path $badesc -Value @("---", 'title: "invalid\q escape"', "---", "", "body") -Encoding UTF8
    $lintOut4 = & $cli -Command lint -WikiRoot $root 2>&1
    $global:LASTEXITCODE = 0
    Assert (($lintOut4 -join "`n") -match "Escape outside kit scalar dialect") "lint detects out-of-dialect escape in raw title"
    Remove-Item $badesc -Force

    # --- T12b: wiki側の方言外エスケープも検出する (V-03) ---
    $badWiki = Join-Path $root "wiki\concepts\BadEscape.md"
    Set-Content -Path $badWiki -Value @("---", 'title: "invalid\q escape"', 'summary: "s"', "sources: []", "---", "", "body") -Encoding UTF8
    $lintOut4b = & $cli -Command lint -WikiRoot $root 2>&1
    $global:LASTEXITCODE = 0
    Assert (($lintOut4b -join "`n") -match "Escape outside kit scalar dialect in title: wiki/concepts/BadEscape.md") "lint detects out-of-dialect escape on wiki side"
    Remove-Item $badWiki -Force

    # --- T12c: 単独CR入りタイトルの往復 (V-02) ---
    & $cli -Command ingest -WikiRoot $root -Text "body" -Title "left`rright" | Out-Null
    $crRaw = Get-ChildItem (Join-Path $root "raw\*.md") | Where-Object { $_.Name -like "*left-right*" } | Select-Object -First 1
    $crLine = (Get-Content $crRaw.FullName -Encoding UTF8)[1]
    Assert ($crLine -eq 'title: "left\rright"') "lone CR escaped as \r in YAML ($crLine)"
    $indexContent2 = Get-Content (Join-Path $root "raw\_index.md") -Raw -Encoding UTF8
    Assert ($indexContent2 -match "\[left right\]\(") "lone CR flattened to space in index label"
    $lintOutCr = & $cli -Command lint -WikiRoot $root 2>&1
    $global:LASTEXITCODE = 0
    Assert (($lintOutCr -join "`n") -match "OK") "lint passes with \r-escaped title (valid dialect)"

    # --- T12d: URLコロン入りの引用符なしtitleは誤検出しない ---
    $urlRaw = Join-Path $root "raw\2026-01-04-url.md"
    Set-Content -Path $urlRaw -Value @("---", "title: https://example.com/page", "---", "", "body") -Encoding UTF8
    $lintOutUrl = & $cli -Command lint -WikiRoot $root 2>&1
    $global:LASTEXITCODE = 0
    Assert (($lintOutUrl -join "`n") -notmatch "colon\+space.*url") "no false positive on plain URL title"
    Remove-Item $urlRaw -Force

    # --- T13: 制御文字入りタイトルがYAMLに漏れない (R-04) ---
    & $cli -Command ingest -WikiRoot $root -Text "body" -Title "bell$([char]7)title" | Out-Null
    $bellRaw = Get-ChildItem (Join-Path $root "raw\*.md") | Where-Object { $_.Name -like "*bell*" } | Select-Object -First 1
    $bellContent = Get-Content $bellRaw.FullName -Raw -Encoding UTF8
    Assert (-not ($bellContent.Contains([string][char]7))) "C0 control char stripped from YAML"

    # --- T14-17: Pythonフック（python が無い環境ではスキップ） ---
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        $hookDir = Join-Path $repo "hooks"
        $event = '{"trigger":"auto","transcript_path":"C:\\Users\\secret\\session.jsonl"}'
        Invoke-PyHook $py.Source (Join-Path $hookDir "precompact_hook.py") $event $work | Out-Null
        $journal = Get-Content (Join-Path $root "inbox\journal.md") -Raw -Encoding UTF8
        $markerOk = $journal -match "PreCompact境界（auto）"
        if (-not $markerOk) {
            # CI診断: 何が起きたかを可視化する
            Write-Output "DIAG: py=$($py.Source) exit=$script:LastHookExit work=$work"
            Write-Output "DIAG: journal tail: $($journal.Substring([Math]::Max(0, $journal.Length - 300)))"
            $diag = Join-Path $root "diagnostics\hooks.log"
            if (Test-Path $diag) { Write-Output "DIAG: hooks.log: $(Get-Content $diag -Raw -Encoding UTF8)" } else { Write-Output "DIAG: no hooks.log" }
        }
        Assert $markerOk "precompact hook appends boundary marker"
        Assert ($journal -notmatch "secret") "transcript path NOT persisted to journal (R-06)"
        $recov = Invoke-PyHook $py.Source (Join-Path $hookDir "wiki_index_hook.py") '{"source":"compact"}' $work
        Assert ($recov -match "コンパクション直後の回復指示") "index hook injects compact recovery"
        # BOM付きstdinでもparseできる（PS 5.1パイプ経路の回帰）
        Invoke-PyHook $py.Source (Join-Path $hookDir "precompact_hook.py") ($([char]0xFEFF) + '{"trigger":"manual"}') $work | Out-Null
        $journal2 = Get-Content (Join-Path $root "inbox\journal.md") -Raw -Encoding UTF8
        Assert ($journal2 -match "PreCompact境界（manual）") "hook parses BOM-prefixed stdin"
    } else {
        Write-Output "SKIP: python not found - hook tests skipped"
    }
}
finally {
    if (Test-Path $work) { Remove-Item -Recurse -Force $work }
}

Write-Output ""
Write-Output "Result: $passed passed, $failed failed"
if ($failed -gt 0) { exit 1 }
