# -*- coding: utf-8 -*-
"""SessionStartフック: LLM Wiki索引をセッション開始時に自動注入する。

「Wikiはpull型＝検索を思い付くかが勘まかせ」という弱点の克服。
索引（タイトル＋1行要約）を毎セッション自動でAIの視界に入れ、本文は必要時に
Readで開く2段構え。道具を「感覚」に変えるための受動注入レイヤ。

- カレントディレクトリから上へ辿って最初に見つかった `.wiki/` を対象にする
  （gitのリポジトリ探索と同じ流儀。環境変数 LLM_WIKI_ROOT があれば最優先）
- 各ページのYAMLフロントマターから title / summary を直接読む（索引ファイルは
  経由しない: 正はページ本体）
- どんな失敗でも黙って何も出さない（フックがセッション開始を壊してはならない）

Claude Code への配線（~/.claude/settings.json）:
    {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
        "command": "python", "args": ["<このファイルへのパス>"], "timeout": 10}]}]}}
"""
import os
import sys
from pathlib import Path

SECTIONS = ["concepts", "syntheses", "entities", "sources"]
MAX_SUMMARY = 160


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


def frontmatter(path):
    """先頭のYAMLフロントマターから title / summary を素朴に拾う（依存ゼロ）。"""
    title = summary = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return "", ""
    if not lines or lines[0].strip() != "---":
        return "", ""
    for line in lines[1:60]:
        if line.strip() == "---":
            break
        if line.startswith("title:"):
            title = line[len("title:"):].strip().strip("'\"")
        elif line.startswith("summary:"):
            summary = line[len("summary:"):].strip().strip("'\"")
    return title, summary


def main():
    root = find_wiki_root()
    if root is None:
        return
    wiki = root / "wiki"
    out = []
    for section in SECTIONS:
        pages = sorted((wiki / section).glob("*.md")) if (wiki / section).is_dir() else []
        for page in pages:
            if page.name.startswith("_"):
                continue
            title, summary = frontmatter(page)
            title = title or page.stem
            if len(summary) > MAX_SUMMARY:
                summary = summary[:MAX_SUMMARY] + "…"
            rel = page.relative_to(root.parent)
            entry = f"- {title} — {summary}" if summary else f"- {title}"
            out.append(f"{entry} [{rel}]")
    if not out:
        return
    print(f"[LLM Wiki索引（自動注入・SessionStart）] 過去の判断・罠・パターンの目録。"
          f"関連しそうな作業のときは該当ページを {root} 配下からReadで開くこと:")
    print("\n".join(out))


if __name__ == "__main__":
    try:
        # WindowsのPython標準出力は既定でCP932＝日本語が化ける。
        # ハーネスはUTF-8で読むため明示的に揃える
        sys.stdout.reconfigure(encoding="utf-8")
        main()
    except Exception:
        sys.exit(0)  # フックは何があってもセッションを壊さない
