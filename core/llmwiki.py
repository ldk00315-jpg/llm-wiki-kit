# -*- coding: utf-8 -*-
"""llmwiki — LLM Wiki maintenance core (Python正本).

設計書 docs/cross-agent-design.md の決定事項に基づく Phase 1 実装:
- 決定#2:  Coreの正本はPython（3.10+・外部パッケージ依存なし）
- 決定#8:  kit経由の書き込みは協調lockで直列化する
- 決定#9:  atomic write（一時ファイル→flush→os.replace）＋変更検知＋conflict file
- 決定#10: F-06最小実装（注入用テキストの正規化・命令文ヒューリスティクスのWARN）
- 決定#13: Coreは意味内容（文字列）を返し、ホスト固有の出力形式はアダプターが包む

出力メッセージ・索引フォーマット・lint検査項目は scripts/llm-wiki.ps1（v1.2.3系）と
互換。PowerShell側は本モジュールへ委譲する互換wrapperになる（決定#2の段階移行）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

VERSION = "1.3.0-dev"

SECTIONS = [
    ("Sources", "sources"),
    ("Entities", "entities"),
    ("Concepts", "concepts"),
    ("Syntheses", "syntheses"),
]

# ---------------------------------------------------------------------------
# 基本ユーティリティ
# ---------------------------------------------------------------------------


def today_str() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


def new_slug(value: str) -> str:
    """ps1 New-Slug と同一: ASCII kebab、退化時はタイトルMD5先頭6桁。"""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        digest = hashlib.md5(value.encode("utf-8")).hexdigest()
        slug = f"note-{digest[:6]}"
    return slug


_C0_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def yaml_scalar_encode(value: str) -> str:
    """常に二重引用符スカラー。\\ \" CRLF/CR/LF/TAB をエスケープ、残るC0は除去。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\r\n", "\\n").replace("\r", "\\r")
    escaped = escaped.replace("\n", "\\n").replace("\t", "\\t")
    escaped = _C0_RE.sub("", escaped)
    return f'"{escaped}"'


_UNESCAPE_MAP = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def yaml_scalar_decode(value: str) -> str:
    """encodeと対称のデコード。未知のエスケープは原文のまま残す。"""
    v = value.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return re.sub(
            r"\\(.)",
            lambda m: _UNESCAPE_MAP.get(m.group(1), m.group(0)),
            v[1:-1],
        )
    if len(v) >= 2 and v.startswith("'") and v.endswith("'"):
        return v[1:-1].replace("''", "'")
    return v


def markdown_label(value: str) -> str:
    """バックスラッシュ→角括弧の順でエスケープ、改行は空白へ。"""
    flat = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    flat = flat.replace("\\", "\\\\")
    return re.sub(r"([\[\]])", r"\\\1", flat)


def sanitize_injection_text(value: str) -> str:
    """F-06: モデルコンテキストへ注入するテキストの正規化。

    C0制御文字（TAB以外）・双方向制御文字・BOMを除去する。
    構文の安全化であり、意味的なprompt injectionの無害化ではない（設計書§5）。
    """
    value = _C0_RE.sub("", value)
    return re.sub(r"[\u202a-\u202e\u2066-\u2069\ufeff]", "", value)


_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(?:(?:all|previous|above|prior)\s+)*(instructions?|context)",
        r"disregard\s+(all|previous|above)",
        r"you\s+are\s+now\s+",
        r"system\s*prompt",
        r"<\s*/?\s*system\b",
        r"BEGIN\s+INSTRUCTIONS",
        r"(前|上記|以前)の指示(を|は)(無視|忘れ)",
        r"システムプロンプト",
        r"(あなたは今から|これ以降あなたは)",
    ]
]


def injection_warnings(text: str) -> list[str]:
    """F-06: 命令文らしきパターンの検出（警告のみ・失敗させない）。"""
    return [p.pattern for p in _INJECTION_PATTERNS if p.search(text)]


# ---------------------------------------------------------------------------
# atomic write / 変更検知 / lock（設計書 決定#8・#9）
# ---------------------------------------------------------------------------


def _snapshot(path: Path):
    """変更検知用スナップショット（mtime_ns + サイズ + SHA-256）。無ければNone。

    content hashを持つため「同じサイズへ編集しmtimeを復元した外部変更」も検出する
    （設計書 決定#9。mtime/sizeだけの判定はPhase 1検証 §3.2 で棄却された）。
    """
    try:
        data = path.read_bytes()
        st = path.stat()
        return (st.st_mtime_ns, len(data), hashlib.sha256(data).hexdigest())
    except FileNotFoundError:
        return None


def atomic_write_text(path: Path, text: str, expect_snapshot=..., conflict_ok: bool = True) -> Path | None:
    """一時ファイル→flush+fsync→os.replace のatomic書き込み。

    expect_snapshot に読込時のスナップショットを渡すと、置換直前に外部変更を
    再確認し、変わっていたら黙って上書きせず `<path>.conflict-<ts>` へ書いて
    その Path を返す（正常時は None）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".llmwiki")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if expect_snapshot is not ... and _snapshot(path) != expect_snapshot:
            # expect_snapshot=None は「存在しないはず」の意味も持つ:
            # check後に外部で同名ファイルが作られたTOCTOU競合もここで検出される
            if not conflict_ok:
                raise RuntimeError(f"Concurrent modification detected: {path}")
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            conflict = path.with_name(
                path.name + f".conflict-{stamp}-{uuid.uuid4().hex[:8]}"
            )
            os.replace(tmp, conflict)
            return conflict
        os.replace(tmp, path)
        return None
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class LockTimeout(RuntimeError):
    pass


class VaultLock:
    """Vault単位の協調lock。lock directoryのatomic creationを利用（決定#9）。

    排他契約（Phase 1検証 §3.1 対応）:
    - `<root>/.lock/` のmkdirが成功した者が所有。取得ごとに固有の owner token
      （UUID）を発行し owner.json に記録する
    - `release()` は owner.json のtokenが自分のものである場合だけ削除する
      —— stale回収後に遅れて戻った旧所有者が、新所有者のlockを壊せない
    - stale回収は「quarantine rename → 削除」: `.lock/` を一意名へatomic renameし、
      renameに成功した1者だけが解体を行う（複数writerの同時回収競合を排除）
    - `refresh()` はlease heartbeat: 長い処理はこれでmtimeを更新し、正当な
      長時間処理がstale扱いで奪取されるのを防ぐ（lock保持中の処理は
      stale_sec より十分短いか、定期的に refresh() を呼ぶこと）
    - 外部エディタ（Obsidian等）はこのlockを守らない＝atomic write側が防衛線
    """

    def __init__(self, root: Path, timeout: float = 10.0, stale_sec: float = 300.0):
        self.lock_dir = Path(root) / ".lock"
        self.timeout = timeout
        self.stale_sec = stale_sec
        self.token = uuid.uuid4().hex
        self._acquired = False

    def _owner_info(self) -> str:
        try:
            return (self.lock_dir / "owner.json").read_text(encoding="utf-8")
        except OSError:
            return "(unknown owner)"

    def _owner_token(self) -> str | None:
        try:
            data = json.loads((self.lock_dir / "owner.json").read_text(encoding="utf-8"))
            return data.get("token")
        except (OSError, ValueError):
            return None

    def _is_stale(self) -> bool:
        try:
            age = time.time() - self.lock_dir.stat().st_mtime
            return age > self.stale_sec
        except OSError:
            return False  # 消えた直後 → 通常の再試行に任せる

    def _reclaim_stale(self) -> None:
        """stale lockの回収。atomic renameに成功した1者だけが解体する。"""
        quarantine = self.lock_dir.with_name(f".lock-stale-{uuid.uuid4().hex[:8]}")
        try:
            os.rename(self.lock_dir, quarantine)
        except OSError:
            return  # 別のwriterが先に回収した（または所有者が動いた）
        shutil.rmtree(quarantine, ignore_errors=True)

    def acquire(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.lock_dir.mkdir()
                owner = {
                    "token": self.token,
                    "pid": os.getpid(),
                    "host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
                    "started": datetime.datetime.now().isoformat(timespec="seconds"),
                    "tool": f"llmwiki {VERSION}",
                }
                (self.lock_dir / "owner.json").write_text(
                    json.dumps(owner, ensure_ascii=False), encoding="utf-8"
                )
                self._acquired = True
                return self
            except FileExistsError:
                if self._is_stale():
                    self._reclaim_stale()
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"Vault lock held by another writer: {self.lock_dir} "
                        f"owner={self._owner_info()}"
                    )
                time.sleep(0.2)

    def refresh(self):
        """lease heartbeat: 保持中のlockのmtimeを更新しstale奪取を防ぐ。"""
        if self._acquired:
            try:
                os.utime(self.lock_dir)
            except OSError:
                pass

    def release(self):
        if not self._acquired:
            return
        self._acquired = False
        # 所有権の検証: stale回収で奪取された後なら、他人のlockには触らない
        if self._owner_token() != self.token:
            return
        try:
            (self.lock_dir / "owner.json").unlink(missing_ok=True)
            self.lock_dir.rmdir()
        except OSError:
            pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


# ---------------------------------------------------------------------------
# Vault構造
# ---------------------------------------------------------------------------


def find_wiki_root(start: Path | None = None) -> Path | None:
    """cwdから上へ辿って `.wiki/wiki` を持つディレクトリを探す（フックと同一契約）。"""
    env = os.environ.get("LLM_WIKI_ROOT")
    if env:
        p = Path(env)
        return p if (p / "wiki").is_dir() else None
    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        candidate = parent / ".wiki"
        if (candidate / "wiki").is_dir():
            return candidate
    return None


def ensure_dirs(root: Path):
    for rel in ["", "raw", "wiki", "wiki/sources", "wiki/entities", "wiki/concepts",
                "wiki/syntheses", "schema", "inbox", "assets"]:
        (root / rel).mkdir(parents=True, exist_ok=True)


def assert_wiki_exists(root: Path):
    """status/lint は読み取り専用: 存在しないWikiを黙って作らない。"""
    if not (root / "wiki").is_dir():
        print(f"ERROR: Wiki root not found: {root} (run 'init' first)")
        sys.exit(1)


def rel_wiki_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def frontmatter_value(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0] != "---":
        return None
    pattern = re.compile("^" + re.escape(key) + r":\s*(.+)$")
    for line in lines[1:]:
        if line == "---":
            break
        m = pattern.match(line)
        if m:
            return yaml_scalar_decode(m.group(1))
    return None


def index_created(path: Path, fallback: str) -> str:
    if path.exists():
        existing = frontmatter_value(path, "created")
        if existing:
            return existing
    return fallback


# ---------------------------------------------------------------------------
# init / index / log
# ---------------------------------------------------------------------------

_OVERVIEW_STUB = """\
---
title: Workspace Overview
summary: Living synthesis and entry point for this wiki.
tags: [meta, entry-point]
sources: []
created: {today}
updated: {today}
confidence: low
---

# Workspace Overview

This page is the entry point. Keep it a short, living synthesis:
which maps (syntheses) exist, what themes recur, what is unresolved.
Rewrite it as the wiki grows -- an entry point that goes stale stops
being an entry point.

## Themes

_None yet. After the first captures, list recurring themes here._

## Open Questions

- _None yet._
"""


def init_vault(root: Path):
    ensure_dirs(root)
    today = today_str()
    seeds = {
        root / "_index.md": (
            "---\ntitle: Workspace LLM Wiki\n"
            "summary: Master index for the workspace-local LLM Wiki.\n"
            f"created: {today}\nupdated: {today}\n---\n\n"
            "# Workspace LLM Wiki\n\n"
            "- [Raw sources](raw/_index.md)\n"
            "- [Synthesized wiki](wiki/_index.md)\n"
            "- [Schema](schema/AGENTS.llm-wiki.md)\n"
            "- [Operation log](log.md)\n"
        ),
        root / "log.md": (
            "---\ntitle: LLM Wiki Log\nsummary: Append-only operation log.\n"
            f"created: {today}\nupdated: {today}\n---\n\n"
            "# LLM Wiki Log\n\n"
            f"## [{today}] init | workspace\n\n"
            "Initialized the workspace-local LLM Wiki.\n"
        ),
        root / "wiki" / "overview.md": _OVERVIEW_STUB.format(today=today),
    }
    with VaultLock(root):
        for path, text in seeds.items():
            if not path.exists():
                # expect_snapshot=None: check後に外部で作られたらconflictへ退避
                conflict = atomic_write_text(path, text, expect_snapshot=None)
                if conflict is not None:
                    print(f"WARN: {path.name} was created concurrently; seed saved to {conflict.name}")
        update_indexes(root, locked=True)


def _iter_raw_files(root: Path) -> list[Path]:
    raw_dir = root / "raw"
    if not raw_dir.is_dir():
        return []
    files = [p for p in raw_dir.rglob("*") if p.is_file() and p.name != "_index.md"]
    return sorted(files, key=lambda p: str(p).lower())


def _iter_article_files(root: Path) -> list[Path]:
    wiki_dir = root / "wiki"
    if not wiki_dir.is_dir():
        return []
    files = [p for p in wiki_dir.rglob("*.md") if p.name != "_index.md"]
    return sorted(files, key=lambda p: str(p).lower())


def update_indexes(root: Path, locked: bool = False):
    if not locked:
        with VaultLock(root):
            return update_indexes(root, locked=True)

    ensure_dirs(root)
    today = today_str()
    raw_dir = root / "raw"
    wiki_dir = root / "wiki"
    raw_files = _iter_raw_files(root)
    article_files = _iter_article_files(root)

    # read-modify-write保護: created:を読む前にsnapshotを取り、書き込み時に
    # 外部変更（Obsidian等・lockを守らない編集者）を検出する（決定#9・検証§3.2）
    raw_index_path = raw_dir / "_index.md"
    wiki_index_path = wiki_dir / "_index.md"
    master_index_path = root / "_index.md"
    snap_raw = _snapshot(raw_index_path)
    snap_wiki = _snapshot(wiki_index_path)
    snap_master = _snapshot(master_index_path)

    raw_created = index_created(raw_index_path, today)
    wiki_created = index_created(wiki_index_path, today)
    master_created = index_created(master_index_path, today)

    raw_lines = [
        "---", "title: Raw Sources",
        "summary: Immutable source catalog for the workspace LLM Wiki.",
        f"created: {raw_created}", f"updated: {today}", "---", "", "# Raw Sources", "",
    ]
    if not raw_files:
        raw_lines.append("No sources ingested yet.")
    else:
        for f in raw_files:
            title = frontmatter_value(f, "title") or f.stem
            rel = rel_wiki_path(root, f)
            raw_lines.append(f"- [{markdown_label(title)}](../{rel})")

    wiki_lines = [
        "---", "title: Synthesized Wiki",
        "summary: Catalog of LLM-maintained synthesis pages.",
        f"created: {wiki_created}", f"updated: {today}", "---", "", "# Synthesized Wiki", "",
    ]
    if (wiki_dir / "overview.md").exists():
        wiki_lines += ["- [Overview](overview.md)", ""]
    for name, subdir in SECTIONS:
        wiki_lines += [f"## {name}", ""]
        cat_dir = wiki_dir / subdir
        if not cat_dir.is_dir():
            wiki_lines += ["_directory missing_", ""]
            continue
        cat_files = sorted(
            [p for p in cat_dir.glob("*.md") if p.name != "_index.md"],
            key=lambda p: p.name.lower(),
        )
        if not cat_files:
            wiki_lines.append("_none yet_")
        else:
            for f in cat_files:
                title = frontmatter_value(f, "title") or f.stem
                summary = frontmatter_value(f, "summary") or "No summary."
                wiki_lines.append(f"- [{markdown_label(title)}]({subdir}/{f.name}) - {summary}")
        wiki_lines.append("")

    master_lines = [
        "---", "title: Workspace LLM Wiki",
        "summary: Master index for the workspace-local LLM Wiki.",
        f"created: {master_created}", f"updated: {today}", "---", "",
        "# Workspace LLM Wiki", "", "## Stats", "",
        f"- Raw sources: {len(raw_files)}",
        f"- Synthesized articles: {len(article_files)}",
        "", "## Navigation", "",
        "- [Raw sources](raw/_index.md)",
        "- [Synthesized wiki](wiki/_index.md)",
        "- [Schema](schema/AGENTS.llm-wiki.md)",
        "- [Operation log](log.md)",
    ]

    for path, lines, snap in [
        (raw_index_path, raw_lines, snap_raw),
        (wiki_index_path, wiki_lines, snap_wiki),
        (master_index_path, master_lines, snap_master),
    ]:
        conflict = atomic_write_text(path, "\n".join(lines) + "\n", expect_snapshot=snap)
        if conflict is not None:
            print(f"WARN: {path.name} changed concurrently; regenerated index saved to {conflict.name}")


def add_log_entry(root: Path, action: str, subject: str, detail: str, locked: bool = False):
    """log.md への追記。読込→追記→スナップショット検証つきatomic置換。"""
    if not locked:
        with VaultLock(root):
            return add_log_entry(root, action, subject, detail, locked=True)
    log_path = root / "log.md"
    snap = _snapshot(log_path)
    existing = log_path.read_text(encoding="utf-8-sig") if log_path.exists() else ""
    entry = f"\n## [{today_str()}] {action} | {subject}\n\n{detail}\n"
    conflict = atomic_write_text(log_path, existing + entry, expect_snapshot=snap)
    if conflict is not None:
        print(f"WARN: log.md changed concurrently; entry saved to {conflict.name}")


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

URL_TIMEOUT_SEC = 30
URL_MAX_BYTES = 5_000_000


def _fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "llm-wiki-kit"})
    with urllib.request.urlopen(req, timeout=URL_TIMEOUT_SEC) as resp:
        data = resp.read(URL_MAX_BYTES + 1)
    if len(data) > URL_MAX_BYTES:
        raise RuntimeError(f"URL content exceeds {URL_MAX_BYTES} bytes: {url}")
    return data.decode("utf-8", errors="replace")


def ingest(root: Path, source: str | None, text: str | None, title: str | None) -> Path:
    if not (source and source.strip()) and not (text and text.strip()):
        raise ValueError("Provide -Source or -Text for ingest.")

    today = today_str()
    if not title:
        # ps1と同じ導出: 拡張子を除いたファイル名（URLも同じ規則）
        title = Path(source).stem if source else f"inline-note-{today}"

    if text:
        body, source_label, kind = text, "inline", "text"
    elif re.match(r"^https?://", source):
        body, source_label, kind = _fetch_url(source), source, "url"
    else:
        resolved = Path(source).resolve()
        body = resolved.read_text(encoding="utf-8-sig", errors="replace")
        source_label, kind = str(resolved), "file"

    h1 = title.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    h1 = _C0_RE.sub("", h1)
    content = (
        "---\n"
        f"title: {yaml_scalar_encode(title)}\n"
        f"source: {yaml_scalar_encode(source_label)}\n"
        f"kind: {kind}\n"
        f"ingested: {today}\n"
        "status: raw\n"
        "---\n\n"
        f"# {h1}\n\n"
        f"{body}\n"
    )

    with VaultLock(root) as lock:
        ensure_dirs(root)
        slug = new_slug(title)
        target = root / "raw" / f"{today}-{slug}.md"
        i = 2
        while target.exists():
            target = root / "raw" / f"{today}-{slug}-{i}.md"
            i += 1
        # expect_snapshot=None: 一意名の選定後に外部で同名が作られたら退避
        conflict = atomic_write_text(target, content, expect_snapshot=None)
        if conflict is not None:
            target = conflict
            print(f"WARN: raw target was created concurrently; saved as {conflict.name}")
        lock.refresh()
        update_indexes(root, locked=True)
        add_log_entry(
            root, "ingest", title,
            f"Saved raw source to {rel_wiki_path(root, target)}. Synthesis still needs an agent pass.",
            locked=True,
        )
    return target


# ---------------------------------------------------------------------------
# lint（ps1 v1.2.3系と同一の検査項目 + F-06 WARN）
# ---------------------------------------------------------------------------

_FM_CLOSED_RE = re.compile(r"^---\r?\n.*?\r?\n---(\r?\n|$)", re.DOTALL)


def _has_out_of_dialect_escape(text: str) -> bool:
    """kit方言（\\n \\t \\r \\\" \\\\）外のエスケープを対走査で検出する。

    v1.2.3のps1実装は1文字ずつの先読みで判定していたため、`\\\\end` のような
    「エスケープ済みバックスラッシュ＋通常文字」を偽陽性にしていた（本実装で修正）。
    """
    i = 0
    while i < len(text):
        if text[i] == "\\":
            if i + 1 >= len(text) or text[i + 1] not in 'ntr"\\':
                return True
            i += 2
        else:
            i += 1
    return False


def _check_frontmatter(content: str, rel: str, issues: list[str]):
    if not _FM_CLOSED_RE.match(content):
        issues.append(f"Unterminated frontmatter (no closing ---): {rel}")
    for key in ("title", "summary"):
        if re.search(rf'(?m)^{key}:\s*(?!")[^"\r\n]*:(\s|\r|$)', content):
            issues.append(f"Unquoted 'colon+space' in {key} (invalid YAML): {rel}")
        qm = re.search(rf'(?m)^{key}:\s*"(.*)"\s*$', content)
        if qm and _has_out_of_dialect_escape(qm.group(1)):
            issues.append(f"Escape outside kit scalar dialect in {key}: {rel}")
    head = content[:2000]
    if _C0_RE.search(head):
        issues.append(f"Control character in frontmatter region: {rel}")


def lint(root: Path) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    required = ["_index.md", "raw/_index.md", "wiki/_index.md", "wiki/overview.md",
                "schema/AGENTS.llm-wiki.md", "log.md"]
    for rel in required:
        if not (root / rel).exists():
            issues.append(f"Missing required file: {rel}")

    for f in _iter_article_files(root):
        rel = rel_wiki_path(root, f)
        content = f.read_text(encoding="utf-8-sig", errors="replace")
        if not content.startswith("---"):
            issues.append(f"Missing frontmatter: {rel}")
            continue
        _check_frontmatter(content, rel, issues)
        if not re.search(r"(?m)^sources:\s*(\[.*\]|\r?\n(\s+-\s.+\r?\n)+)", content):
            issues.append(f"Missing sources frontmatter: {rel}")
        # F-06: 索引注入されるsummaryに命令文らしきパターンがないか（警告のみ）
        summary = frontmatter_value(f, "summary") or ""
        for pat in injection_warnings(summary):
            warnings.append(f"WARN(F-06): instruction-like text in summary ({pat}): {rel}")

    raw_dir = root / "raw"
    if raw_dir.is_dir():
        for f in [p for p in raw_dir.rglob("*.md") if p.name != "_index.md"]:
            rel = rel_wiki_path(root, f)
            content = f.read_text(encoding="utf-8-sig", errors="replace")
            if not content.startswith("---"):
                issues.append(f"Raw file missing frontmatter: {rel}")
                continue
            _check_frontmatter(content, rel, issues)

    return issues, warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llmwiki", description="LLM Wiki maintenance core")
    parser.add_argument("command", choices=["init", "status", "ingest", "reindex", "lint"])
    parser.add_argument("--wiki-root", default=".wiki")
    parser.add_argument("--source", default=None)
    parser.add_argument("--text", default=None)
    parser.add_argument("--title", default=None)
    args = parser.parse_args(argv)

    # PowerShell wrapper は値を環境変数で渡す（PS 5.1の引用符エスケープ問題の回避）。
    # コマンドライン引数が明示されていれば引数が優先
    if args.wiki_root == ".wiki" and os.environ.get("LLMWIKI_WIKI_ROOT"):
        args.wiki_root = os.environ["LLMWIKI_WIKI_ROOT"]
    args.source = args.source or os.environ.get("LLMWIKI_SOURCE")
    args.text = args.text or os.environ.get("LLMWIKI_TEXT")
    args.title = args.title or os.environ.get("LLMWIKI_TITLE")

    root = Path(args.wiki_root)
    try:
        if args.command == "init":
            init_vault(root)
            print(f"Initialized {root}")
        elif args.command == "status":
            assert_wiki_exists(root)
            raw_count = len(_iter_raw_files(root))
            article_count = len(_iter_article_files(root))
            print(f"Wiki root: {root.resolve()}")
            print(f"Raw sources: {raw_count}")
            print(f"Synthesized articles: {article_count}")
        elif args.command == "ingest":
            try:
                target = ingest(root, args.source, args.text, args.title)
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            print(f"Ingested: {target}")
        elif args.command == "reindex":
            with VaultLock(root):
                update_indexes(root, locked=True)
                add_log_entry(root, "reindex", "workspace",
                              "Rebuilt master, raw, and wiki indexes.", locked=True)
            print("Rebuilt indexes.")
        elif args.command == "lint":
            assert_wiki_exists(root)
            issues, warnings = lint(root)
            for w in warnings:
                print(w)
            if not issues:
                print("OK: structural checks passed (limited check, not a full YAML validation).")
            else:
                for issue in issues:
                    print(f"ISSUE: {issue}")
                return 1
    except LockTimeout as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
