# -*- coding: utf-8 -*-
"""SessionStartフック（Claude Codeアダプター）: LLM Wiki索引を注入する。

「Wikiはpull型＝検索を思い付くかが勘まかせ」という弱点の克服。
索引（タイトル＋1行要約）を毎セッション自動でAIの視界に入れ、本文は必要時に
Readで開く2段構え。道具を「感覚」に変えるための受動注入レイヤ。

Phase 2 でこのファイルは**薄いアダプター**になった（設計書 決定#13）。
索引テキストの組み立ては Core の `build_index_context()` が行い、本ファイルの
責務はホスト固有の部分だけに限定される:

- Claude Code のイベント判定（`source == "compact"` → 回復ブロック要求）
- stdin/stdout のエンコーディング処理
- 失敗時の安全な沈黙＋診断ログ

**やってはいけないこと**: 索引テキストの生成・整形、delimiterの改変、
戻り値の切り詰め（すべてCoreの責務。契約は docs/core-api-contract.md）。

Claude Code は SessionStart フックの stdout をそのままコンテキストへ追加する
ため、Coreの戻り値を平文で出力する（Codexアダプターは同じ文字列をJSONで包む）。

- カレントディレクトリから上へ辿って最初に見つかった `.wiki/` を対象にする
  （gitのリポジトリ探索と同じ流儀。環境変数 LLM_WIKI_ROOT があれば最優先）
- `.wiki/` が見つからない場所では黙って何もしない
- 失敗時は `.wiki/diagnostics/hooks.log` に記録を試み（それも失敗したら沈黙）、
  セッション開始を壊さない

Claude Code への配線（~/.claude/settings.json）:
    {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
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
    from llmwiki import build_index_context, find_wiki_root  # type: ignore
except Exception:  # noqa: BLE001 — Coreが無い環境では沈黙する
    build_index_context = None  # type: ignore
    find_wiki_root = None  # type: ignore

MAX_OUTPUT = 8000


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
            f.write(f"[{stamp}] wiki_index_hook: {message}\n")
    except Exception:
        pass


def main():
    if build_index_context is None or find_wiki_root is None:
        return
    root = find_wiki_root()
    if root is None:
        return
    event = read_stdin_event()
    # ホスト固有の判定はここだけ: Claude Code は要約直後の再開を
    # SessionStart の source == "compact" で通知する
    compact = event.get("source") == "compact"
    context = build_index_context(root, compact_recovery=compact, max_chars=MAX_OUTPUT)
    if context:
        # Claude Code は stdout をそのままコンテキストへ追加する（平文でよい）
        print(context)


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
        sys.exit(0)  # フックは何があってもセッションを壊さない
