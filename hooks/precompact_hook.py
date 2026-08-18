# -*- coding: utf-8 -*-
"""PreCompactフック（Claude Codeアダプター）: 保全境界をディスクへ確定記録する。

問題: 要約は「何をしたか」を残し「なぜ・細部・失敗過程」＝wiki級知見の肉を
最初に削る。しかも知見が一番溜まっている瞬間（コンテキスト肥大時）と要約で
消える直前は同一タイミング。定時の棚卸しだけでは要約を跨いだ知見が失われる。

対策（このフックは3層防御の環境側レイヤ）:
1. 即時書き出し — 知見は解決直後に書く（スキーマの Auto-capture 節）
2. inbox/journal.md — 「wiki級かも」の瞬間に1行だけ落とすWAL（先行ログ）
3. このフック — 要約直前に journal.md へ「PreCompact境界マーカー」を追記する。
   コンテキストと違ってディスクは要約で消えない。回復は相棒の
   wiki_index_hook.py（SessionStart）が担う: source=="compact" で再開した
   セッションに「journalを確認し未記録知見を書き出せ」を注入する。

Phase 2 でこのファイルは**薄いアダプター**になった（設計書 決定#13）。
マーカーの文言と journal への追記（lock＋atomic write＋変更検知）は Core が担い、
本ファイルの責務はホスト固有の部分だけに限定される:

- Claude Code のイベント入力から `trigger` を取り出す
- stdin のエンコーディング処理
- 失敗時の安全な沈黙＋診断ログ

設計メモ: v1.1は `hookSpecificOutput.additionalContext` で要約者へ指示を渡す
設計だったが、公式仕様で PreCompact は additionalContext の注入対象イベントに
含まれないことを確認し、**未保証チャネルへの出力を決定論的なディスク永続化に
置き換えた**。公式に文書化された回復経路は「SessionStart フックの compact
ソース」であり、本キットはそれに乗る。

lock が取れなかった場合はマーカーを諦めて診断ログに残す（fail-closed）。
知見の耐久性の本体は即時書き出しとWALであり、このフックは安全網なので、
1回書けなくても破綻しない。

- カレントディレクトリから上へ辿って最初に見つかった `.wiki/` を対象にする
- `.wiki/` が見つからない場所では黙って何もしない
- どんな失敗でもコンパクションを壊さない（exit 0）

Claude Code への配線（~/.claude/settings.json）:
    {"hooks": {"PreCompact": [{"hooks": [{"type": "command",
        "command": "python", "args": ["<このファイルへのパス>"], "timeout": 10}]}]}}

要求ランタイム: Python 3.10+（外部パッケージ依存なし）
"""
import datetime
import json
import os
import sys
from pathlib import Path

# Core の探索: 同居（配備先）→ ../core（キットのレイアウト）の順
for _candidate in (Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent / "core"):
    if (_candidate / "llmwiki.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

try:
    from llmwiki import (  # type: ignore
        LockTimeout,
        append_compact_boundary_marker,
        find_wiki_root,
    )
except Exception:  # noqa: BLE001 — Coreが無い環境では沈黙する
    append_compact_boundary_marker = None  # type: ignore
    find_wiki_root = None  # type: ignore

    class LockTimeout(RuntimeError):  # type: ignore
        pass


def read_stdin_event() -> dict:
    """フック入力（JSON）を読む。無ければ空dict（手動実行でも壊れない）。"""
    try:
        if sys.stdin.isatty():
            return {}
        # バイトで読む: sys.stdin.encoding はlocale依存(cp1252等)で、ホストが
        # 送るUTF-8 JSONを化かすことがある。またPowerShell(.NET)のパイプは
        # UTF-8 BOM(EF BB BF)を先頭に付けることがある(コンソール設定依存)。
        # バイトレベルでBOMを剥がし、localeと無関係にUTF-8でデコードする
        raw_b = sys.stdin.buffer.read()
        utf8_bom = bytes([0xEF, 0xBB, 0xBF])
        while raw_b.startswith(utf8_bom):
            raw_b = raw_b[3:]
        raw = raw_b.decode("utf-8", errors="replace").lstrip("﻿").strip()
        event = json.loads(raw) if raw else {}
        # 配列や文字列が来ても呼び出し側の .get() を壊さない
        return event if isinstance(event, dict) else {}
    except Exception:
        return {}


def _fallback_wiki_root():
    """Coreをimportできないときだけ使う、最小限のroot探索（診断ログ用）。"""
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


def log_failure(root, message: str) -> None:
    """診断ログへの追記を試みる。失敗しても例外を出さない。"""
    try:
        if root is None:
            return
        diag = Path(root) / "diagnostics"
        diag.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(diag / "hooks.log", "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] precompact_hook: {message}\n")
    except Exception:
        pass


def main():
    if append_compact_boundary_marker is None or find_wiki_root is None:
        return
    root = find_wiki_root()
    if root is None:
        return
    event = read_stdin_event()
    # ホスト固有の判定はここだけ（Claude Code / Codex とも trigger: manual|auto）
    trigger = event.get("trigger", "unknown")
    try:
        conflict = append_compact_boundary_marker(root, trigger)
    except LockTimeout as exc:
        # 他のwriterが作業中。マーカーは諦める（本体は即時capture＋WAL）
        log_failure(root, f"journal locked, marker skipped: {exc}")
        return
    if conflict is not None:
        log_failure(root, f"journal changed concurrently; marker saved to {conflict.name}")


if __name__ == "__main__":
    try:
        # WindowsのPython標準出力は既定でCP932＝日本語が化ける。
        # ハーネスはUTF-8で読むため明示的に揃える
        sys.stdout.reconfigure(encoding="utf-8")
        main()
    except Exception as exc:
        try:
            log_failure(find_wiki_root() if find_wiki_root else _fallback_wiki_root(), repr(exc))
        except Exception:
            pass
        sys.exit(0)  # フックは何があってもコンパクションを壊さない
