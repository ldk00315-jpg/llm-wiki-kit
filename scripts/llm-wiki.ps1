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

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-Today {
    return (Get-Date).ToString("yyyy-MM-dd")
}

function New-Slug {
    param([string]$Value)

    $slug = $Value.ToLowerInvariant()
    $slug = [regex]::Replace($slug, "[^a-z0-9]+", "-")
    $slug = $slug.Trim("-")
    if ([string]::IsNullOrWhiteSpace($slug)) {
        # 非ASCIIタイトル（日本語等）はASCII slugに退化する。無情報な固定名
        # "source" で衝突・意味喪失させず、タイトルのハッシュで識別可能にする
        $md5 = [System.Security.Cryptography.MD5]::Create()
        $bytes = $md5.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Value))
        $hex = -join ($bytes[0..2] | ForEach-Object { $_.ToString("x2") })
        $slug = "note-$hex"
    }
    return $slug
}

function ConvertTo-YamlScalar {
    param([string]$Value)

    # 依存ゼロのYAMLエスケープ: 常に二重引用符スカラーとして出力する。
    # コロン・引用符・改行・バックスラッシュを含む正当なタイトルでも
    # frontmatterが壊れない（YAML double-quoted styleの仕様に準拠）
    $escaped = $Value -replace '\\', '\\'
    $escaped = $escaped -replace '"', '\"'
    $escaped = $escaped -replace "`r`n", '\n'
    $escaped = $escaped -replace "`n", '\n'
    $escaped = $escaped -replace "`t", '\t'
    # YAMLはC0制御文字（TAB/LF/CR以外）を非エスケープでは許さない。
    # ここまでで改行・タブは処理済みなので、残る制御文字は除去する
    $escaped = [regex]::Replace($escaped, '[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '')
    return '"' + $escaped + '"'
}

function ConvertFrom-YamlScalar {
    param([string]$Value)

    # ConvertTo-YamlScalar と対称のデコード。二重引用符スカラーなら
    # 外側を外してエスケープを解決し、それ以外は素の値を返す
    $v = $Value.Trim()
    if ($v.Length -ge 2 -and $v.StartsWith('"') -and $v.EndsWith('"')) {
        $inner = $v.Substring(1, $v.Length - 2)
        return [regex]::Replace($inner, '\\(.)', {
            param($m)
            switch ($m.Groups[1].Value) {
                'n' { "`n" }
                't' { "`t" }
                default { $m.Groups[1].Value }
            }
        })
    }
    if ($v.Length -ge 2 -and $v.StartsWith("'") -and $v.EndsWith("'")) {
        return $v.Substring(1, $v.Length - 2).Replace("''", "'")
    }
    return $v
}

function ConvertTo-MarkdownLabel {
    param([string]$Value)

    # Markdownリンクのラベル用: バックスラッシュ→角括弧の順でエスケープし、
    # 改行を潰す（順序が逆だと角括弧用の\が二重エスケープされる）
    $flat = $Value -replace "`r`n", " " -replace "`n", " "
    $flat = $flat -replace '\\', '\\'
    return $flat -replace '([\[\]])', '\$1'
}

function Ensure-Wiki {
    param([string]$Root)

    $dirs = @(
        $Root,
        (Join-Path $Root "raw"),
        (Join-Path $Root "wiki"),
        (Join-Path $Root "wiki\sources"),
        (Join-Path $Root "wiki\entities"),
        (Join-Path $Root "wiki\concepts"),
        (Join-Path $Root "wiki\syntheses"),
        (Join-Path $Root "schema"),
        (Join-Path $Root "inbox"),
        (Join-Path $Root "assets")
    )

    foreach ($dir in $dirs) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Force $dir | Out-Null
        }
    }
}

function Get-RelativeWikiPath {
    param(
        [string]$Root,
        [string]$Path
    )

    $rootFull = (Resolve-Path $Root).Path.TrimEnd("\")
    $full = (Resolve-Path $Path).Path
    if ($full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $full.Substring($rootFull.Length + 1).Replace("\", "/")
    }
    return $full.Replace("\", "/")
}

function Get-IndexCreated {
    param(
        [string]$Path,
        [string]$Fallback
    )

    if (Test-Path $Path) {
        $existing = Get-FrontmatterValue $Path "created"
        if ($existing) { return $existing }
    }
    return $Fallback
}

function Get-FrontmatterValue {
    param(
        [string]$Path,
        [string]$Key
    )

    $lines = Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction Stop
    if ($lines.Count -eq 0 -or $lines[0] -ne "---") {
        return $null
    }

    for ($i = 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -eq "---") {
            break
        }
        if ($lines[$i] -match ("^" + [regex]::Escape($Key) + ":\s*(.+)$")) {
            return ConvertFrom-YamlScalar $Matches[1]
        }
    }

    return $null
}

function Write-TextFile {
    param(
        [string]$Path,
        [string[]]$Lines
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines((Resolve-Path -LiteralPath (Split-Path $Path -Parent)).Path + "\" + (Split-Path $Path -Leaf), $Lines, $utf8NoBom)
}

function Initialize-Wiki {
    param([string]$Root)

    Ensure-Wiki $Root
    $today = Get-Today

    $files = @{
        (Join-Path $Root "_index.md") = @(
            "---",
            "title: Workspace LLM Wiki",
            "summary: Master index for the workspace-local LLM Wiki.",
            "created: $today",
            "updated: $today",
            "---",
            "",
            "# Workspace LLM Wiki",
            "",
            "- [Raw sources](raw/_index.md)",
            "- [Synthesized wiki](wiki/_index.md)",
            "- [Schema](schema/AGENTS.llm-wiki.md)",
            "- [Operation log](log.md)"
        )
        (Join-Path $Root "log.md") = @(
            "---",
            "title: LLM Wiki Log",
            "summary: Append-only operation log.",
            "created: $today",
            "updated: $today",
            "---",
            "",
            "# LLM Wiki Log",
            "",
            "## [$today] init | workspace",
            "",
            "Initialized the workspace-local LLM Wiki."
        )
        (Join-Path $Root "wiki\overview.md") = @(
            "---",
            "title: Workspace Overview",
            "summary: Living synthesis and entry point for this wiki.",
            "tags: [meta, entry-point]",
            "sources: []",
            "created: $today",
            "updated: $today",
            "confidence: low",
            "---",
            "",
            "# Workspace Overview",
            "",
            "This page is the entry point. Keep it a short, living synthesis:",
            "which maps (syntheses) exist, what themes recur, what is unresolved.",
            "Rewrite it as the wiki grows -- an entry point that goes stale stops",
            "being an entry point.",
            "",
            "## Themes",
            "",
            "_None yet. After the first captures, list recurring themes here._",
            "",
            "## Open Questions",
            "",
            "- _None yet._"
        )
    }

    foreach ($path in $files.Keys) {
        if (-not (Test-Path $path)) {
            Write-TextFile $path $files[$path]
        }
    }

    Update-Indexes $Root
}

function Update-Indexes {
    param([string]$Root)

    Ensure-Wiki $Root
    $today = Get-Today
    $rawDir = Join-Path $Root "raw"
    $wikiDir = Join-Path $Root "wiki"

    $rawFiles = @(Get-ChildItem -LiteralPath $rawDir -File -Recurse | Where-Object { $_.Name -ne "_index.md" } | Sort-Object FullName)
    $articleFiles = @(Get-ChildItem -LiteralPath $wikiDir -File -Filter "*.md" -Recurse | Where-Object { $_.Name -ne "_index.md" } | Sort-Object FullName)

    # 索引の created: は既存値を引き継ぐ（無ければ今日＝ハードコードしない）
    $rawCreated = Get-IndexCreated (Join-Path $rawDir "_index.md") $today
    $wikiCreated = Get-IndexCreated (Join-Path $wikiDir "_index.md") $today
    $masterCreated = Get-IndexCreated (Join-Path $Root "_index.md") $today

    $rawLines = @(
        "---",
        "title: Raw Sources",
        "summary: Immutable source catalog for the workspace LLM Wiki.",
        "created: $rawCreated",
        "updated: $today",
        "---",
        "",
        "# Raw Sources",
        ""
    )

    if ($rawFiles.Count -eq 0) {
        $rawLines += "No sources ingested yet."
    } else {
        foreach ($file in $rawFiles) {
            $title = Get-FrontmatterValue $file.FullName "title"
            if (-not $title) { $title = $file.BaseName }
            $rel = Get-RelativeWikiPath $Root $file.FullName
            $rawLines += "- [$(ConvertTo-MarkdownLabel $title)](../$rel)"
        }
    }

    $wikiLines = @(
        "---",
        "title: Synthesized Wiki",
        "summary: Catalog of LLM-maintained synthesis pages.",
        "created: $wikiCreated",
        "updated: $today",
        "---",
        "",
        "# Synthesized Wiki",
        ""
    )
    if (Test-Path (Join-Path $wikiDir "overview.md")) {
        $wikiLines += "- [Overview](overview.md)"
        $wikiLines += ""
    }

    $categories = @(
        @{ Name = "Sources";    Dir = "sources" },
        @{ Name = "Entities";   Dir = "entities" },
        @{ Name = "Concepts";   Dir = "concepts" },
        @{ Name = "Syntheses";  Dir = "syntheses" }
    )

    foreach ($cat in $categories) {
        $catDir = Join-Path $wikiDir $cat.Dir
        $wikiLines += "## $($cat.Name)"
        $wikiLines += ""
        if (-not (Test-Path $catDir)) {
            $wikiLines += "_directory missing_"
            $wikiLines += ""
            continue
        }
        $catFiles = @(Get-ChildItem -LiteralPath $catDir -File -Filter "*.md" | Where-Object { $_.Name -ne "_index.md" } | Sort-Object Name)
        if ($catFiles.Count -eq 0) {
            $wikiLines += "_none yet_"
        } else {
            foreach ($file in $catFiles) {
                $title = Get-FrontmatterValue $file.FullName "title"
                $summary = Get-FrontmatterValue $file.FullName "summary"
                if (-not $title) { $title = $file.BaseName }
                if (-not $summary) { $summary = "No summary." }
                $rel = "$($cat.Dir)/$($file.Name)"
                $wikiLines += "- [$(ConvertTo-MarkdownLabel $title)]($rel) - $summary"
            }
        }
        $wikiLines += ""
    }

    $masterLines = @(
        "---",
        "title: Workspace LLM Wiki",
        "summary: Master index for the workspace-local LLM Wiki.",
        "created: $masterCreated",
        "updated: $today",
        "---",
        "",
        "# Workspace LLM Wiki",
        "",
        "## Stats",
        "",
        "- Raw sources: $($rawFiles.Count)",
        "- Synthesized articles: $($articleFiles.Count)",
        "",
        "## Navigation",
        "",
        "- [Raw sources](raw/_index.md)",
        "- [Synthesized wiki](wiki/_index.md)",
        "- [Schema](schema/AGENTS.llm-wiki.md)",
        "- [Operation log](log.md)"
    )

    Write-TextFile (Join-Path $rawDir "_index.md") $rawLines
    Write-TextFile (Join-Path $wikiDir "_index.md") $wikiLines
    Write-TextFile (Join-Path $Root "_index.md") $masterLines
}

function Add-LogEntry {
    param(
        [string]$Root,
        [string]$Action,
        [string]$Subject,
        [string]$Detail
    )

    $logPath = Join-Path $Root "log.md"
    $today = Get-Today
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $entry = [string[]]@("", "## [$today] $Action | $Subject", "", $Detail)
    $logFull = (Resolve-Path -LiteralPath (Split-Path $logPath -Parent)).Path + "\" + (Split-Path $logPath -Leaf)
    [System.IO.File]::AppendAllLines($logFull, $entry, $utf8NoBom)
}

function Invoke-Ingest {
    param(
        [string]$Root,
        [string]$SourcePath,
        [string]$InlineText,
        [string]$SourceTitle
    )

    if ([string]::IsNullOrWhiteSpace($SourcePath) -and [string]::IsNullOrWhiteSpace($InlineText)) {
        throw "Provide -Source or -Text for ingest."
    }

    Ensure-Wiki $Root
    $today = Get-Today

    if (-not $SourceTitle) {
        if ($SourcePath) {
            $SourceTitle = [System.IO.Path]::GetFileNameWithoutExtension($SourcePath)
        } else {
            $SourceTitle = "inline-note-$today"
        }
    }

    $slug = New-Slug $SourceTitle
    $target = Join-Path (Join-Path $Root "raw") "$today-$slug.md"
    $i = 2
    while (Test-Path $target) {
        $target = Join-Path (Join-Path $Root "raw") "$today-$slug-$i.md"
        $i++
    }

    if ($InlineText) {
        $body = $InlineText
        $sourceLabel = "inline"
        $kind = "text"
    } elseif ($SourcePath -match "^https?://") {
        $response = Invoke-WebRequest -Uri $SourcePath -UseBasicParsing
        $body = $response.Content
        $sourceLabel = $SourcePath
        $kind = "url"
    } else {
        $resolved = Resolve-Path -LiteralPath $SourcePath
        $body = Get-Content -LiteralPath $resolved.Path -Raw -Encoding UTF8
        $sourceLabel = $resolved.Path
        $kind = "file"
    }

    $h1Title = $SourceTitle -replace "`r`n", " " -replace "`n", " "
    $h1Title = [regex]::Replace($h1Title, '[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '')
    $lines = @(
        "---",
        "title: $(ConvertTo-YamlScalar $SourceTitle)",
        "source: $(ConvertTo-YamlScalar $sourceLabel)",
        "kind: $kind",
        "ingested: $today",
        "status: raw",
        "---",
        "",
        "# $h1Title",
        "",
        $body
    )

    Write-TextFile $target $lines
    Update-Indexes $Root
    Add-LogEntry $Root "ingest" $SourceTitle "Saved raw source to $(Get-RelativeWikiPath $Root $target). Synthesis still needs an agent pass."

    Write-Output "Ingested: $target"
}

function Assert-WikiExists {
    param([string]$Root)

    # status/lint は読み取り専用: 存在しないWikiを黙って作らない（副作用禁止）
    if (-not (Test-Path (Join-Path $Root "wiki"))) {
        Write-Output "ERROR: Wiki root not found: $Root (run 'init' first)"
        exit 1
    }
}

function Invoke-Lint {
    param([string]$Root)

    Assert-WikiExists $Root
    $issues = New-Object System.Collections.Generic.List[string]
    $required = @("_index.md", "raw/_index.md", "wiki/_index.md", "wiki/overview.md", "schema/AGENTS.llm-wiki.md", "log.md")

    foreach ($rel in $required) {
        if (-not (Test-Path (Join-Path $Root $rel))) {
            $issues.Add("Missing required file: $rel")
        }
    }

    $articleFiles = @(Get-ChildItem -LiteralPath (Join-Path $Root "wiki") -File -Filter "*.md" -Recurse | Where-Object { $_.Name -ne "_index.md" })
    foreach ($file in $articleFiles) {
        $rel = Get-RelativeWikiPath $Root $file.FullName
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        if (-not $content.StartsWith("---")) {
            $issues.Add("Missing frontmatter: $rel")
        } elseif ($content -notmatch "(?s)^---\r?\n.*?\r?\n---(\r?\n|$)") {
            $issues.Add("Unterminated frontmatter (no closing ---): $rel")
        }
        if ($content -notmatch "(?m)^sources:\s*(\[.*\]|\r?\n(\s+-\s.+\r?\n)+)") {
            $issues.Add("Missing sources frontmatter: $rel")
        }
    }

    # raw の frontmatter 破損検査
    $rawDir = Join-Path $Root "raw"
    if (Test-Path $rawDir) {
        $rawFiles = @(Get-ChildItem -LiteralPath $rawDir -File -Filter "*.md" -Recurse | Where-Object { $_.Name -ne "_index.md" })
        foreach ($file in $rawFiles) {
            $rel = Get-RelativeWikiPath $Root $file.FullName
            $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
            if (-not $content.StartsWith("---")) {
                $issues.Add("Raw file missing frontmatter: $rel")
                continue
            }
            if ($content -notmatch "(?s)^---\r?\n.*?\r?\n---(\r?\n|$)") {
                $issues.Add("Unterminated frontmatter (no closing ---): $rel")
            }
            if ($content -match "(?m)^title:\s*(?!`")([^`"\r\n]*:.*)$") {
                # 引用符なしtitleにコロン → YAMLとして不正な可能性が高い
                $issues.Add("Unquoted colon in title (invalid YAML): $rel")
            }
            $titleMatch = [regex]::Match($content, "(?m)^title:\s*`"(.*)`"\s*$")
            if ($titleMatch.Success) {
                $quoted = $titleMatch.Groups[1].Value
                # 二重引用符スカラー内の不正エスケープ（\n \t \" \\ 以外）
                if ([regex]::IsMatch($quoted, '\\(?![nt"\\])')) {
                    $issues.Add("Invalid escape in quoted title: $rel")
                }
            }
            # YAMLが許さないC0制御文字（TAB以外）
            if ([regex]::IsMatch($content.Substring(0, [Math]::Min($content.Length, 2000)), '[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')) {
                $issues.Add("Control character in frontmatter region: $rel")
            }
        }
    }

    if ($issues.Count -eq 0) {
        Write-Output "OK: structural checks passed (limited check, not a full YAML validation)."
    } else {
        $issues | ForEach-Object { Write-Output "ISSUE: $_" }
        exit 1
    }
}

switch ($Command) {
    "init" {
        Initialize-Wiki $WikiRoot
        Write-Output "Initialized $WikiRoot"
    }
    "status" {
        Assert-WikiExists $WikiRoot
        $rawCount = @(Get-ChildItem -LiteralPath (Join-Path $WikiRoot "raw") -File -Recurse | Where-Object { $_.Name -ne "_index.md" }).Count
        $articleCount = @(Get-ChildItem -LiteralPath (Join-Path $WikiRoot "wiki") -File -Filter "*.md" -Recurse | Where-Object { $_.Name -ne "_index.md" }).Count
        Write-Output "Wiki root: $((Resolve-Path $WikiRoot).Path)"
        Write-Output "Raw sources: $rawCount"
        Write-Output "Synthesized articles: $articleCount"
    }
    "ingest" {
        Invoke-Ingest $WikiRoot $Source $Text $Title
    }
    "reindex" {
        Update-Indexes $WikiRoot
        Add-LogEntry $WikiRoot "reindex" "workspace" "Rebuilt master, raw, and wiki indexes."
        Write-Output "Rebuilt indexes."
    }
    "lint" {
        Invoke-Lint $WikiRoot
    }
}

