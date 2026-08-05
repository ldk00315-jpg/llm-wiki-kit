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
        $slug = "source"
    }
    return $slug
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
            return $Matches[1].Trim().Trim('"')
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

    $rawLines = @(
        "---",
        "title: Raw Sources",
        "summary: Immutable source catalog for the workspace LLM Wiki.",
        "created: 2026-04-27",
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
            $rawLines += "- [$title](../$rel)"
        }
    }

    $wikiLines = @(
        "---",
        "title: Synthesized Wiki",
        "summary: Catalog of LLM-maintained synthesis pages.",
        "created: 2026-04-27",
        "updated: $today",
        "---",
        "",
        "# Synthesized Wiki",
        "",
        "- [Overview](overview.md)",
        ""
    )

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
                $wikiLines += "- [$title]($rel) - $summary"
            }
        }
        $wikiLines += ""
    }

    $masterLines = @(
        "---",
        "title: Workspace LLM Wiki",
        "summary: Master index for the workspace-local LLM Wiki.",
        "created: 2026-04-27",
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

    $lines = @(
        "---",
        "title: $SourceTitle",
        "source: $sourceLabel",
        "kind: $kind",
        "ingested: $today",
        "status: raw",
        "---",
        "",
        "# $SourceTitle",
        "",
        $body
    )

    Write-TextFile $target $lines
    Update-Indexes $Root
    Add-LogEntry $Root "ingest" $SourceTitle "Saved raw source to $(Get-RelativeWikiPath $Root $target). Synthesis still needs an agent pass."

    Write-Output "Ingested: $target"
}

function Invoke-Lint {
    param([string]$Root)

    Ensure-Wiki $Root
    $issues = New-Object System.Collections.Generic.List[string]
    $required = @("_index.md", "raw/_index.md", "wiki/_index.md", "schema/AGENTS.llm-wiki.md", "log.md")

    foreach ($rel in $required) {
        if (-not (Test-Path (Join-Path $Root $rel))) {
            $issues.Add("Missing required file: $rel")
        }
    }

    $articleFiles = @(Get-ChildItem -LiteralPath (Join-Path $Root "wiki") -File -Filter "*.md" -Recurse | Where-Object { $_.Name -ne "_index.md" })
    foreach ($file in $articleFiles) {
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        if (-not $content.StartsWith("---")) {
            $issues.Add("Missing frontmatter: $(Get-RelativeWikiPath $Root $file.FullName)")
        }
        if ($content -notmatch "(?m)^sources:\s*(\[.*\]|\r?\n(\s+-\s.+\r?\n)+)") {
            $issues.Add("Missing sources frontmatter: $(Get-RelativeWikiPath $Root $file.FullName)")
        }
    }

    if ($issues.Count -eq 0) {
        Write-Output "OK: no structural issues found."
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
        Ensure-Wiki $WikiRoot
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

