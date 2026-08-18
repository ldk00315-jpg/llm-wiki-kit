# -*- coding: utf-8 -*-
"""Codex SessionStart adapter for LLM Wiki index context.

This file owns Codex-specific transport only. Context construction, filtering,
delimiters, and the output budget belong to Core's ``build_index_context()``.

Runtime requirements: Python 3.10+, no third-party packages.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path


# Core lookup: colocated deployment first, repository layout second.
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parent.parent / "core"):
    if (_candidate / "llmwiki.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

try:
    from llmwiki import build_index_context, find_wiki_root  # type: ignore
except Exception:  # noqa: BLE001 - a missing/broken Core must not break startup
    build_index_context = None  # type: ignore[assignment]
    find_wiki_root = None  # type: ignore[assignment]


MAX_OUTPUT = 8000
UTF8_BOM = b"\xef\xbb\xbf"


def read_stdin_event() -> dict:
    """Read Codex's UTF-8 JSON event, tolerating one or more UTF-8 BOMs."""
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.buffer.read()
        while raw.startswith(UTF8_BOM):
            raw = raw[len(UTF8_BOM):]
        text = raw.decode("utf-8", errors="replace").lstrip("\ufeff").strip()
        event = json.loads(text) if text else {}
        return event if isinstance(event, dict) else {}
    except Exception:  # noqa: BLE001 - malformed input degrades to normal startup
        return {}


def _fallback_wiki_root() -> Path | None:
    """Best-effort root discovery used only when Core cannot be imported."""
    try:
        env = os.environ.get("LLM_WIKI_ROOT")
        if env:
            candidate = Path(env)
            return candidate if (candidate / "wiki").is_dir() else None
        current = Path.cwd().resolve()
        for parent in (current, *current.parents):
            candidate = parent / ".wiki"
            if (candidate / "wiki").is_dir():
                return candidate
    except Exception:
        pass
    return None


def log_failure(root: Path | None, message: str) -> None:
    """Attempt a diagnostic append; logging failure is deliberately silent."""
    try:
        if root is None:
            return
        diagnostics = root / "diagnostics"
        diagnostics.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(diagnostics / "hooks.log", "a", encoding="utf-8") as stream:
            stream.write(f"[{stamp}] codex_session_start: {message}\n")
    except Exception:
        pass


def main() -> None:
    if build_index_context is None or find_wiki_root is None:
        return

    root = find_wiki_root()
    if root is None:
        return

    event = read_stdin_event()
    context = build_index_context(
        root,
        compact_recovery=event.get("source") == "compact",
        max_chars=MAX_OUTPUT,
    )
    if not context:
        return

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    # JSON encoding preserves multiline context and literal angle brackets.
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        main()
    except Exception as exc:  # noqa: BLE001 - hooks must never break session start
        try:
            root = find_wiki_root() if find_wiki_root else _fallback_wiki_root()
            log_failure(root, repr(exc))
        except Exception:
            pass

