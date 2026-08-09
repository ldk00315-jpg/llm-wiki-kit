# -*- coding: utf-8 -*-
"""PreCompactフック: コンパクション（会話要約）直前に知見保全指示を注入する。

問題: 要約は「何をしたか」を残し「なぜ・細部・失敗過程」＝wiki級知見の肉を
最初に削る。しかも知見が一番溜まっている瞬間（コンテキスト肥大時）と要約で
消える直前は同一タイミング。定時の棚卸しだけでは要約を跨いだ知見が失われる。

対策（このフックは3層防御の環境側レイヤ）:
1. 即時書き出し — 知見は解決直後に書く（スキーマの Auto-capture 節）
2. inbox/journal.md — 「wiki級かも」の瞬間に1行だけ落とすWAL（先行ログ）
3. このフック — 要約直前にコンテキスト末尾へ2つの指示を注入する:
   (a) 要約者へ「wiki級知見の詳細を要約に残せ」
   (b) 要約後のモデルへ「最初のターンで未記録分を .wiki/ へ書き出せ」

注: PreCompactフックの出力を要約者へ渡す公式チャネルはドキュメントに明記が
無い（2026-08時点）。これは「additionalContext がコンテキスト末尾に載れば、
会話全体を読む要約者の視界に入る」という構造に基づく設計で、素通りしても
(b) が要約後のモデルに拾われる二段構え。

- カレントディレクトリから上へ辿って最初に見つかった `.wiki/` を対象にする
  （wiki_index_hook.py と同じ流儀。環境変数 LLM_WIKI_ROOT があれば最優先）
- `.wiki/` が見つからない場所では黙って何もしない
- どんな失敗でも黙って何も出さない（フックがコンパクションを壊してはならない）

Claude Code への配線（~/.claude/settings.json）:
    {"hooks": {"PreCompact": [{"hooks": [{"type": "command",
        "command": "python", "args": ["<このファイルへのパス>"], "timeout": 10}]}]}}
"""
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


def main():
    root = find_wiki_root()
    if root is None:
        return
    instruction = (
        "[知見保全指示（PreCompactフック自動注入）] これからこの会話は要約されます。"
        "要約者への指示: このセッションで得られた未記録のwiki級知見"
        "（罠・原則・非自明な因果・実測の細部・失敗と回避の過程・採用しなかった案とその理由）は、"
        "要約の中に **具体的な詳細ごと** 残すこと。「何をしたか」だけに圧縮して"
        "「なぜ・どう嵌って・どう抜けたか」を落とさない。"
        f"要約後のモデルへの指示: 要約を受け取った最初のターンで、wiki級知見が要約に"
        f"残っていれば {root} へ書き出すこと"
        f"（迷うものは {root / 'inbox' / 'journal.md'} に1行ポインタでよい）。"
        f"手順は {root / 'schema' / 'AGENTS.llm-wiki.md'} の Auto-capture 節を参照。"
    )
    print(json.dumps({
        "systemMessage": "📚 wiki級知見の保全指示を要約者へ注入",
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": instruction,
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        # WindowsのPython標準出力は既定でCP932＝日本語が化ける。
        # ハーネスはUTF-8で読むため明示的に揃える
        sys.stdout.reconfigure(encoding="utf-8")
        main()
    except Exception:
        sys.exit(0)  # フックは何があってもコンパクションを壊さない
