# -*- coding: utf-8 -*-
"""PreCompactフック: コンパクション（会話要約）直前に、保全境界をディスクへ確定記録する。

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

設計メモ（2026-08-12改訂）: v1.1は `hookSpecificOutput.additionalContext` で
要約者へ指示を渡す設計だったが、公式仕様で PreCompact は additionalContext の
注入対象イベントに含まれないことを確認し、**未保証チャネルへの出力を
決定論的なディスク永続化に置き換えた**。公式に文書化された回復経路は
「SessionStart フックの compact ソース」であり、本キットはそれに乗る。

- カレントディレクトリから上へ辿って最初に見つかった `.wiki/` を対象にする
  （wiki_index_hook.py と同じ流儀。環境変数 LLM_WIKI_ROOT があれば最優先）
- `.wiki/` が見つからない場所では黙って何もしない
- 失敗時は `.wiki/diagnostics/hooks.log` に記録を試み（それも失敗したら沈黙）、
  exit 0 でコンパクションを壊さない

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


def find_wiki_root() -> Path | None:
    """cwdから上へ辿って `.wiki/wiki` を持つディレクトリを探す。"""
    env = os.environ.get("LLM_WIKI_ROOT")
    if env:
        p = Path(env)
        return p if (p / "wiki").is_dir() else None
    cur = Path.cwd()
    for parent in [cur, *cur.parents]:
        candidate = parent / ".wiki"
        if (candidate / "wiki").is_dir():
            return candidate
    return None


def read_stdin_event() -> dict:
    """フック入力（JSON）を読む。無ければ空dict（手動実行でも壊れない）。"""
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def log_failure(root: Path | None, message: str) -> None:
    """診断ログへの追記を試みる。失敗しても例外を出さない。"""
    try:
        if root is None:
            return
        diag = root / "diagnostics"
        diag.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(diag / "hooks.log", "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] precompact_hook: {message}\n")
    except Exception:
        pass


def main():
    root = find_wiki_root()
    if root is None:
        return
    event = read_stdin_event()
    trigger = event.get("trigger", "unknown")
    transcript = event.get("transcript_path", "")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    marker = (
        f"- [{stamp}] **PreCompact境界（{trigger}）** — この行より上の未処理エントリと、"
        f"直前セッションの未記録wiki級知見を、コンパクション後の最初のターンで"
        f"ページ化すること。"
    )
    if transcript:
        marker += f" transcript: {transcript}"

    journal = root / "inbox" / "journal.md"
    journal.parent.mkdir(exist_ok=True)
    with open(journal, "a", encoding="utf-8") as f:
        f.write(marker + "\n")


if __name__ == "__main__":
    try:
        # WindowsのPython標準出力は既定でCP932＝日本語が化ける。
        # ハーネスはUTF-8で読むため明示的に揃える
        sys.stdout.reconfigure(encoding="utf-8")
        main()
    except Exception as exc:
        try:
            log_failure(find_wiki_root(), repr(exc))
        except Exception:
            pass
        sys.exit(0)  # フックは何があってもコンパクションを壊さない
