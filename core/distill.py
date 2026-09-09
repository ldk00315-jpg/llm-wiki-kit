#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""wiki-distill — Wiki→Skill蒸留トラック（D）の CLI / library（merge 2）。

契約: `docs/distillation-contract.md`。schema: `schema/distill/*.schema.json`。

  python core/distill.py nominate <Page>   --reason "..."     # 人の指名（frontmatter付与＋event）
  python core/distill.py status [--distill-id d-xxxxxxxx]     # read-only（state と候補一覧）
  python core/distill.py decide <distill_id> <held|rejected|accepted> --reason "..."
  python core/distill.py rereview <distill_id> --reason "..."  # 人の再レビュー（state は変えない）
  python core/distill.py note --type opportunity|invoked|completed|blocked ...
  python core/distill.py reindex                              # distill/_index.md の決定論的再生成
  python core/distill.py validate [--refs --ref-base id=dir]  # event 集合と派生 index の invariant 検査

設計の要点:
- event は `<vault>/distill/events/<event_id>.json` へ **exclusive create**（更新禁止）。
  ID 衝突は乱数を引き直して最大 3 回まで再試行する
- **state を変える event（observed / nominated / decision）は review 済み page identity にしか出せない**
- state 変更は VaultLock 取得後に head（最新 state event）と `previous_event_id / previous_event_sha256 /
  expected_previous_state` の一致を再検査してから書く（C-07）
- `distill/_index.md` は派生物。参照整合性は authoritative record（proposal / manifest）へ照合し、
  index は「再生成と一致するか」だけを検査する（C-03）
- resolver: `base_id` を canonical resolve し、結合後の解決 path が base 配下であることを確認する
  （`..` の字句拒否だけに頼らず、symlink / junction 経由の脱出も fail-closed）
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llmwiki import (  # noqa: E402  （Core の lock / atomic write / frontmatter 契約を再利用）
    VaultLock,
    _frontmatter_lines,
    atomic_write_text,
    find_wiki_root,
    yaml_scalar_decode,
)

CONTRACT_VERSION = "0.1"
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema" / "distill"

EVENT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
OPPORTUNITY_ID_RE = re.compile(r"^op-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
DISTILL_ID_RE = re.compile(r"^d-[0-9a-f]{8}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# portable_path（schema と同一の字句規則。segment 単位で . / .. / 空 / 制御文字を拒否）
# R3-2: segment の**先頭・末尾の空白**も拒否する（Windows の path alias）。
# 末尾ドット（`proposal.md.`）は字句としては通す——それを owner 検査の迂回に使えないことは
# `normalize_portable` を通した**等値**比較で保証する（「同じ file に着地する」を根拠に通さない）
_SEG = r"(?!\.\.?(?:/|$))(?! )[^/\\\x00-\x1f\x7f-\x9f]*[^/\\\x00-\x1f\x7f-\x9f ]"
PORTABLE_PATH_RE = re.compile(r"^(?![A-Za-z]:)(?!~)" + _SEG + r"(?:/" + _SEG + r")*$")
# merge 3（bound_to）の導入時刻。これ**より前**に書かれた accepted だけが legacy（R3-9）
MERGE3_CUTOFF = "2026-09-09T00:00:00Z"   # 文書用。legacy 判定には使わない（R5: G15）
# R5 (G15): legacy と認める「bound_to を持たない accepted」は、merge 3 導入前に CLI が書いた
# **既知の event_id の固定リスト**だけ。occurred_at は書き手が決められる値なので、時刻の窓で
# 判定すると cutoff 前の chain に backdate した accepted を繋いで gate を開けられた。
# このリストは append-only の store に対応する歴史的事実であり、増やすには commit が要る。
# R5 (G19): id だけでは「その名前の file を書けば legacy になる」ので、file bytes の sha256 も添えて
# 等値検査する（偽造するには本物の chain を丸ごと複製するしかなくなる）。
LEGACY_ACCEPTED_EVENTS: dict[str, str] = {
    "20260908T001351Z-c8e02b64":   # 共有 Vault d-6ddce7f6 の accepted（2026-09-08・とんすけ）
        "d68394d9187dbfbca31203389447c76e433106a6f8a782373790dcc85a01b849",
}
LEGACY_ACCEPTED_EVENT_IDS: frozenset[str] = frozenset(LEGACY_ACCEPTED_EVENTS)   # 後方互換（参照用）

REF_BASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

STATE_EVENTS = ("observed", "nominated", "decision", "rereviewed")
OPPORTUNITY_EVENTS = ("opportunity", "invoked", "completed", "blocked")
CANDIDATE_STATES = ("absent", "observed", "nominated", "held", "rejected", "accepted")
# 遷移の正本（契約 §3 の表）: event_type -> {(source, from_state)} -> to_state
# rereviewed は **state を変えない**（new_state は expected_previous_state と同値。等値検査は builtin_validate_event）
TRANSITIONS = {
    "observed": {"source": "system", "from": ("absent",), "to": ("observed",)},
    "nominated": {"source": "human", "from": ("absent", "observed", "held"), "to": ("nominated",)},
    "decision": {"source": "human", "from": ("nominated",), "to": ("held", "rejected", "accepted")},
    "rereviewed": {"source": "human", "from": ("nominated", "accepted"), "to": ("nominated", "accepted")},
}
BINDING_EVENTS = ("decision", "rereviewed")     # bound_to を持てる event_type（new_state=accepted のときだけ）
BOUND_TO_KEYS = ("proposal", "effect_contract", "candidate_bundle")
VAULT_REF_ROOTS = ("wiki", "distill")           # ref path の先頭がこれなら Vault root 配下（それ以外は base id）
TERMINAL_EVENTS = ("completed", "blocked")
COUNTED_STRENGTHS = ("observed", "asserted")   # 閾値へ算入できる証拠強度（C-05）
DEFAULT_WINDOW_DAYS = 30
DEFAULT_MIN_OPPORTUNITIES = 3


class DistillError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 基本ユーティリティ
# ---------------------------------------------------------------------------

def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_event_id() -> str:
    return f"{_stamp()}-{secrets.token_hex(4)}"


def new_opportunity_id() -> str:
    return f"op-{_stamp()}-{secrets.token_hex(4)}"


def new_distill_id() -> str:
    return f"d-{secrets.token_hex(4)}"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(Path(p).read_bytes())


def distill_dir(root: Path) -> Path:
    return Path(root) / "distill"


def events_dir(root: Path) -> Path:
    return distill_dir(root) / "events"


# ---------------------------------------------------------------------------
# resolver（契約 §10）: base_id + portable_path -> 実 path（base 配下を再検査）
# ---------------------------------------------------------------------------

# R4-1: Windows の大小文字畳み込みは **ASCII の A-Z だけ**。`str.lower()` / `os.path.normcase` は
# U+212A（KELVIN SIGN）などを 'k' に畳むが、NTFS はそれを別の名前として扱う——「等しい」と判定した
# path が別 file に解決してしまう。非 ASCII の文字には一切触らない
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def path_identity(path) -> str:
    """path の**等値検査**に使う正規形（R4-1）。

    区切りは `/`、大小文字の畳み込みは **Windows で ASCII A-Z だけ**。resolve 先が同じ file でも、
    正規形が一致しなければ「同じ path」とは認めない（path alias で owner 検査を迂回されないため）。
    `os.path.normcase` / `str.lower()` は使わない（非 ASCII を NTFS と違う畳み方で潰すため）。
    """
    if not isinstance(path, str):
        return ""
    s = path.replace("\\", "/")
    return s.translate(_ASCII_FOLD) if os.name == "nt" else s


def normalize_portable(path) -> str:
    """portable path の**字句**正規形（区切り `/` ＋ Windows では `str.lower()`）。

    **等値検査には使わない**（R4-1）。`str.lower()` は U+212A のような非 ASCII を NTFS と異なる形で
    畳むので、identity の判定は `path_identity` を使うこと。
    """
    if not isinstance(path, str):
        return ""
    s = path.replace("\\", "/")
    return s.lower() if os.name == "nt" else s


def resolve_under_base(base: Path, rel: str) -> Path:
    """`rel`（portable_path）を `base` 配下へ解決する。字句検査と実解決の二層。

    - 字句: `PORTABLE_PATH_RE`（ドライブ文字・`~`・先頭 `/`・`.`/`..`/空 segment・制御文字・`\\` を拒否）
    - 実解決: base と結合した path を `resolve()` し、base の解決結果の配下であることを比較する。
      symlink / junction 経由の脱出はここで fail-closed になる。
    """
    if not isinstance(rel, str) or not PORTABLE_PATH_RE.match(rel):
        raise DistillError(f"not a portable path: {rel!r}")
    base_resolved = Path(base).resolve()
    target = (base_resolved / rel).resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise DistillError(f"path escapes base: {rel!r}")
    return target


# ---------------------------------------------------------------------------
# frontmatter（page identity）
# ---------------------------------------------------------------------------

def read_frontmatter(path: Path) -> dict:
    """frontmatter の単純 key: value を dict で返す（Core の行範囲契約を再利用）。"""
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    out = {}
    for line in _frontmatter_lines(lines):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if m:
            out[m.group(1)] = yaml_scalar_decode(m.group(2).strip())
    return out


# ---------------------------------------------------------------------------
# frontmatter の限定 YAML サブセット parser（R2-2）
# ---------------------------------------------------------------------------

class _YamlUnsupported(Exception):
    """対応しない構文に当たった（unparseable）。

    途中まで読めた分は**返さない**ために例外で全体を捨てる（「黙って部分的に読まない」）。
    """


UNPARSEABLE_PREFIX = "unparseable: "     # problems の先頭に付ける印（呼び出し側の分岐に使う）

_YAML_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r", "/": "/"}
_YAML_INT_RE = re.compile(r"^[+-]?[0-9]+$")
_YAML_FLOAT_RE = re.compile(r"^[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_YAML_DIGITS_RE = re.compile(r"^[0-9]+$")
_YAML_REVISION_RE = re.compile(r"^[0-9]+\.[0-9]+$")
_YAML_REJECT_HEAD = "&*!|>"              # anchor / alias / tag / block scalar の開始文字
_YAML_MAX_DEPTH = 32
_YAML_MAX_FLOW_DEPTH = 8                 # 1行 JSON の入れ子上限（R3-12）


class _YamlLine:
    """comment と改行を落とした frontmatter の1行（indent は空白の桁数）。"""

    __slots__ = ("indent", "content", "lineno")

    def __init__(self, indent: int, content: str, lineno: int):
        self.indent, self.content, self.lineno = indent, content, lineno


def _yaml_trailing_comment(content: str, start: int) -> str:
    """`start` 以降で「空白に続く `#`」からをコメントとして落とす（token の**外側**専用）。"""
    for j in range(start, len(content)):
        if content[j] == "#" and (j == 0 or content[j - 1] in " \t"):
            return content[:j].rstrip()
    return content.rstrip()


def _yaml_flow_end(content: str, start: int) -> int:
    """`content[start]` の flow に対応する閉じ括弧の**次**の index（閉じていなければ行末）。"""
    depth, quote, n = 0, None, len(content)
    j = start
    while j < n:
        c = content[j]
        if quote is not None:
            if quote == '"' and c == "\\":
                j += 2
                continue
            if c == quote:
                if quote == "'" and j + 1 < n and content[j + 1] == "'":
                    j += 2
                    continue
                quote = None
            j += 1
            continue
        if c in "\"'":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _yaml_strip_comment(content: str, lineno: int) -> str:
    """コメントを落とす。**クォート判定は token の先頭文字だけ**で行う（R3-6）。

    `note: it's fine` の `'` は plain scalar の一部であって、クォートの開始ではない
    （以前は行全体を走査して「クォート未閉」と誤判定し、proposal 全体を unparseable にしていた）。
    token の先頭が `'` / `"` のときだけクォート scalar として読み、閉じていなければ unparseable。
    plain token の中では、`#` の**直前が空白**のときだけコメントとして扱う。
    """
    n = len(content)
    i = 0
    while i < n and content[i] == "-" and (i + 1 == n or content[i + 1] in " \t"):
        i += 1                                   # sequence marker（`- ` の後ろが token の先頭）
        while i < n and content[i] in " \t":
            i += 1
    while True:
        if i >= n:
            return content.rstrip()
        c = content[i]
        if c == "#":
            return content[:i].rstrip()
        if c in "\"'":
            end = _yaml_quote_end(content[i:])
            if end < 0:
                raise _YamlUnsupported(f"{lineno} 行目: クォートが行内で閉じていません"
                                       "（複数行 scalar / 複数行 flow は対応しません）")
            i += end + 1
            rest = content[i:]
            if rest.startswith(":") and (len(rest) == 1 or rest[1] in " \t"):
                i += 1                            # クォートされた key。値の先頭へ進む
                while i < n and content[i] in " \t":
                    i += 1
                continue
            return _yaml_trailing_comment(content, i)
        if c in "[{":
            return _yaml_trailing_comment(content, _yaml_flow_end(content, i))
        j = i                                     # plain token
        while j < n:
            ch = content[j]
            if ch == "#" and j > 0 and content[j - 1] in " \t":
                return content[:j].rstrip()
            if ch == ":" and (j + 1 == n or content[j + 1] in " \t"):
                break                             # key separator。値の先頭へ進む
            j += 1
        if j >= n:
            return content.rstrip()
        i = j + 1
        while i < n and content[i] in " \t":
            i += 1


def _yaml_prepare(body: list[str], first_lineno: int) -> list[_YamlLine]:
    """frontmatter の生行を (indent, content) へ正規化する。対応しない行はここで弾く。"""
    out: list[_YamlLine] = []
    for offset, raw in enumerate(body):
        lineno = first_lineno + offset
        line = raw.rstrip("\r")
        lead = re.match(r"^[ \t]*", line).group(0)
        if "\t" in lead:
            raise _YamlUnsupported(f"{lineno} 行目: タブインデントは対応しません")
        content = _yaml_strip_comment(line[len(lead):], lineno)
        if content == "":
            continue
        if content in ("---", "...") or content.startswith("%"):
            raise _YamlUnsupported(f"{lineno} 行目: 複数ドキュメント / directive は対応しません")
        out.append(_YamlLine(len(lead), content, lineno))
    return out


def _yaml_quote_end(s: str) -> int:
    """`s[0]` のクォートに対応する閉じクォートの index（見つからなければ -1）。"""
    q, i, n = s[0], 1, len(s)
    while i < n:
        c = s[i]
        if q == '"' and c == "\\":
            i += 2
            continue
        if c == q:
            if q == "'" and i + 1 < n and s[i + 1] == "'":
                i += 2
                continue
            return i
        i += 1
    return -1


def _yaml_quoted(s: str, lineno: int) -> str:
    """クォート scalar を decode する。**クォートの外に文字が残っていたら unparseable**。"""
    end = _yaml_quote_end(s)
    if end < 0:
        raise _YamlUnsupported(f"{lineno} 行目: クォートが閉じていません")
    if end != len(s) - 1:
        raise _YamlUnsupported(f"{lineno} 行目: クォートの外に文字が残っています")
    body = s[1:end]
    if s[0] == "'":
        return body.replace("''", "'")
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n:
            nxt = body[i + 1]
            out.append(_YAML_ESCAPES.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _json_pairs(pairs, lineno: int) -> dict:
    """1行 JSON の object。**key 重複は unparseable**（後勝ちで黙って落とさない・R3-12）。"""
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise _YamlUnsupported(f"{lineno} 行目: flow mapping の key が重複しています")
        out[k] = v
    return out


def _json_constant(name: str, lineno: int):
    """NaN / Infinity / -Infinity は JSON の拡張であり YAML flow ではない（R3-12）。"""
    raise _YamlUnsupported(f"{lineno} 行目: {name} は対応しません（JSON の拡張定数）")


def _json_depth(value, depth: int = 1) -> int:
    if isinstance(value, dict):
        return max([_json_depth(v, depth + 1) for v in value.values()] or [depth])
    if isinstance(value, list):
        return max([_json_depth(v, depth + 1) for v in value] or [depth])
    return depth


def _yaml_flow_split(inner: str, lineno: int) -> list[str]:
    """flow の中身をクォート外の `,` で分ける（入れ子は呼び出し側で拒否済み）。"""
    parts, buf, quote = [], [], None
    i, n = 0, len(inner)
    while i < n:
        c = inner[i]
        if quote is None:
            if c == ",":
                parts.append("".join(buf).strip())
                buf = []
                i += 1
                continue
            if c in "\"'":
                quote = c
            buf.append(c)
            i += 1
            continue
        buf.append(c)
        if quote == '"' and c == "\\" and i + 1 < n:
            buf.append(inner[i + 1])
            i += 2
            continue
        if c == quote:
            if quote == "'" and i + 1 < n and inner[i + 1] == "'":
                buf.append("'")
                i += 2
                continue
            quote = None
        i += 1
    if quote is not None:
        raise _YamlUnsupported(f"{lineno} 行目: flow のクォートが閉じていません")
    parts.append("".join(buf).strip())
    return parts


def _yaml_flow(s: str, lineno: int):
    """1行の flow（`[a, b]` / `{k: v}`）を読む。

    **1行 JSON は JSON として読む**（JSON は YAML flow style の部分集合であり、R1 が規定した
    「1行 JSON flow」の proposal をそのまま受ける）。JSON として読めないときだけ、入れ子なしの
    素朴な flow として読む。
    """
    close = "]" if s[0] == "[" else "}"
    if not s.endswith(close):
        raise _YamlUnsupported(f"{lineno} 行目: flow が1行で閉じていません（複数行 flow は対応しません）")
    try:
        value = json.loads(s, object_pairs_hook=lambda pairs: _json_pairs(pairs, lineno),
                           parse_constant=lambda name: _json_constant(name, lineno))
    except ValueError:
        value = None
    else:
        if _json_depth(value) > _YAML_MAX_FLOW_DEPTH:
            raise _YamlUnsupported(f"{lineno} 行目: 1行 flow の入れ子が深すぎます"
                                   f"（上限 {_YAML_MAX_FLOW_DEPTH}）")
        return value
    inner = s[1:-1].strip()
    if inner == "":
        return [] if close == "]" else {}
    if any(ch in inner for ch in "[]{}"):
        raise _YamlUnsupported(f"{lineno} 行目: 入れ子の flow は対応しません"
                               "（1行で書くなら JSON として妥当な形にしてください）")
    parts = _yaml_flow_split(inner, lineno)
    if close == "]":
        for part in parts:
            _yaml_flow_token(part, lineno)
        return [_yaml_scalar(part, lineno) for part in parts]
    out = {}
    for part in parts:
        split = _yaml_split_key(part)
        if split is None:
            raise _YamlUnsupported(f"{lineno} 行目: flow mapping の `k: v` として読めません")
        _yaml_flow_token(split[0], lineno)
        _yaml_flow_token(split[1], lineno)
        key = _yaml_key_text(split[0], lineno)
        if key in out:
            raise _YamlUnsupported(f"{lineno} 行目: flow mapping の key が重複しています")
        out[key] = _yaml_scalar(split[1], lineno) if split[1] != "" else None
    return out


def _yaml_flow_token(tok: str, lineno: int) -> None:
    """flow の中の token は「完全なクォート」か「クォートを含まないプレーン」だけ（壊れた JSON を通さない）。"""
    if tok == "":
        return
    if tok[0] in "\"'":
        if _yaml_quote_end(tok) != len(tok) - 1:
            raise _YamlUnsupported(f"{lineno} 行目: flow のクォートが閉じていません")
        return
    if '"' in tok or "'" in tok:
        raise _YamlUnsupported(f"{lineno} 行目: flow の token にクォートが混ざっています")


def _yaml_scalar(s: str, lineno: int):
    """scalar を読む（型: bool / null / int / float / それ以外は文字列。**日付は文字列のまま**）。"""
    s = s.strip()
    if s == "":
        return None
    head = s[0]
    if head in _YAML_REJECT_HEAD:
        raise _YamlUnsupported(f"{lineno} 行目: anchor / alias / tag / block scalar は対応しません")
    if s == "-" or s.startswith("- ") or s.startswith("? "):
        raise _YamlUnsupported(f"{lineno} 行目: 行内の入れ子 sequence / 複合 key は対応しません")
    if head in "\"'":
        return _yaml_quoted(s, lineno)
    if head in "[{":
        return _yaml_flow(s, lineno)
    if s in ("null", "Null", "NULL", "~"):
        return None
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    # R3-7 / R3-11 / R3-15: 桁や表記が意味を持つ plain scalar は**文字列のまま**にする。
    # 数値化すると復元できない（sha256 の 64桁・先頭 0 の識別子・版番号 0.1 と 0.10 の別）
    if _YAML_DIGITS_RE.match(s) and (len(s) == 64 or (len(s) > 1 and s[0] == "0")):
        return s
    if _YAML_REVISION_RE.match(s):
        return s
    if _YAML_INT_RE.match(s):
        try:
            return int(s)
        except ValueError:                      # pragma: no cover（regex 済みで到達しない）
            return s
    if ("." in s or "e" in s or "E" in s) and _YAML_FLOAT_RE.match(s):
        try:
            return float(s)
        except ValueError:                      # pragma: no cover
            return s
    return s                                    # `2026-09-05` などはここ（date 型にしない）


def _yaml_split_key(content: str):
    """`key: value` に分けられるなら `(key_raw, value_text)` を返す。scalar 行なら None。

    先頭がクォート / flow のときは、**完全なクォート token の直後が `:`** のときだけ mapping と見る
    （`- "未着: blocked"` のような scalar を mapping に読み違えない）。
    """
    head = content[:1]
    if head in "[{":
        return None
    if head in "\"'":
        end = _yaml_quote_end(content)
        if end < 0:
            return None
        rest = content[end + 1:]
        if rest.startswith(":") and (len(rest) == 1 or rest[1] in " \t"):
            return content[:end + 1], rest[1:].strip()
        return None
    # R3-14: 正規表現（`[^:]+?\s*:`）は1行の長さに対して二乗だった。**線形**に走査する。
    # key は `:` を含めないので、separator になり得るのは**最初の** `:` だけ
    i = content.find(":")
    if i <= 0:
        return None
    if i + 1 < len(content) and content[i + 1] not in " \t":
        return None
    return content[:i].rstrip(), content[i + 1:].strip()


def _yaml_key_text(raw: str, lineno: int) -> str:
    """mapping の key を文字列として得る（key は常に文字列にする）。"""
    if raw[:1] in "\"'":
        return _yaml_quoted(raw, lineno)
    if raw[:1] in _YAML_REJECT_HEAD or raw.startswith("? "):
        raise _YamlUnsupported(f"{lineno} 行目: anchor / alias / tag / 複合 key は対応しません")
    return raw.strip()


def _yaml_is_dash(ln: _YamlLine) -> bool:
    return ln.content == "-" or ln.content.startswith("- ")


def _yaml_block(items: list[_YamlLine], i: int, indent: int, depth: int):
    if depth > _YAML_MAX_DEPTH:
        raise _YamlUnsupported(f"{items[i].lineno} 行目: 入れ子が深すぎます")
    if _yaml_is_dash(items[i]):
        return _yaml_seq(items, i, indent, depth)
    return _yaml_map(items, i, indent, depth)


def _yaml_map(items: list[_YamlLine], i: int, indent: int, depth: int):
    out, n = {}, len(items)
    while i < n and items[i].indent == indent and not _yaml_is_dash(items[i]):
        ln = items[i]
        split = _yaml_split_key(ln.content)
        if split is None:
            raise _YamlUnsupported(f"{ln.lineno} 行目: mapping の `key: value` として読めません")
        key = _yaml_key_text(split[0], ln.lineno)
        rest = split[1]
        i += 1
        if rest == "":
            if i < n and items[i].indent > indent:
                value, i = _yaml_block(items, i, items[i].indent, depth + 1)
            elif i < n and items[i].indent == indent and _yaml_is_dash(items[i]):
                value, i = _yaml_seq(items, i, indent, depth + 1)   # 親 key と同じ桁の sequence
            else:
                value = None
        else:
            value = _yaml_scalar(rest, ln.lineno)
            if i < n and items[i].indent > indent:
                raise _YamlUnsupported(f"{items[i].lineno} 行目: 同じ key に値と入れ子の両方があります")
        if key in out:
            raise _YamlUnsupported(f"{ln.lineno} 行目: key が重複しています")
        out[key] = value
    if i < n and items[i].indent > indent:
        raise _YamlUnsupported(f"{items[i].lineno} 行目: インデントが揃っていません")
    if i < n and items[i].indent == indent and _yaml_is_dash(items[i]):
        raise _YamlUnsupported(f"{items[i].lineno} 行目: 同じ階層に mapping と sequence が混在しています")
    return out, i


def _yaml_seq(items: list[_YamlLine], i: int, indent: int, depth: int):
    out, n = [], len(items)
    while i < n and items[i].indent == indent and _yaml_is_dash(items[i]):
        ln = items[i]
        after = ln.content[1:]
        offset = indent + 1 + (len(after) - len(after.lstrip(" ")))
        rest = after.strip()
        if rest == "":
            i += 1
            if i < n and items[i].indent > indent:
                value, i = _yaml_block(items, i, items[i].indent, depth + 1)
            else:
                value = None
            out.append(value)
            continue
        if _yaml_split_key(rest) is None:                 # scalar item
            out.append(_yaml_scalar(rest, ln.lineno))
            i += 1
            continue
        items[i] = _YamlLine(offset, rest, ln.lineno)     # `- key: v` は offset 桁の mapping 開始
        value, i = _yaml_block(items, i, offset, depth + 1)
        out.append(value)
    if i < n and items[i].indent > indent:
        raise _YamlUnsupported(f"{items[i].lineno} 行目: インデントが揃っていません")
    return out, i


def parse_frontmatter_yaml_subset(text) -> tuple[dict | None, list[str]]:
    """frontmatter を **YAML の限定サブセット**として読む（total: 例外を出さない）。

    返り値は `(doc, problems)`。対応しない構文に当たったら `doc=None` と `unparseable: …` を返す
    （**部分的に読めた分は返さない**）。built-in が正本という作法どおり、外部の YAML 実装には依存しない。

    対応する構文（これだけ）:
      - ブロック mapping（`key: value`・インデントで入れ子）
      - ブロック sequence（`- item`・item は scalar でも mapping でもよい）
      - scalar: プレーン / ダブルクォート（`\\"` `\\\\` `\\n` `\\t` `\\r` `\\/`）/ シングルクォート（`''`）
      - 1行の flow（`[a, b]` / `{k: v}`）。1行 JSON はそのまま JSON として読む（R1 互換）
      - クォート外の `#` コメント・空行
      - 型: クォート無しの `true/false/null/整数/小数` は型付き。それ以外は文字列
        （**`2026-09-05` のような日付は文字列のまま**。PyYAML の date 化を再現しない）

    対応しないもの（`unparseable`）: anchor / alias / tag / 複数ドキュメント / `|` `>` のブロック scalar /
    複数行 flow / タブインデント / 行内の入れ子 sequence / 複合 key / インデント不整合 / key 重複。

    **保証しないこと**: これは YAML 一般の parser ではない。上の一覧に無い構文を「読めた」とは言わない。
    """
    if not isinstance(text, str):
        return None, [UNPARSEABLE_PREFIX + "frontmatter が文字列ではありません"]
    lines = text.splitlines()
    body = _frontmatter_lines(lines)
    if not body:
        return None, [UNPARSEABLE_PREFIX + "frontmatter がありません（opening / closing の --- が要ります）"]
    try:
        items = _yaml_prepare(body, 2)
        if not items:
            return {}, []
        doc, i = _yaml_block(items, 0, items[0].indent, 0)
        if i < len(items):
            raise _YamlUnsupported(f"{items[i].lineno} 行目: インデントが揃っていません")
        if not isinstance(doc, dict):
            raise _YamlUnsupported("frontmatter の root が mapping ではありません")
        return doc, []
    except _YamlUnsupported as e:
        return None, [UNPARSEABLE_PREFIX + _safe(str(e), 200)]
    except RecursionError:
        return None, [UNPARSEABLE_PREFIX + "入れ子が深すぎます"]
    except Exception as e:                        # total: どんな入力でも例外を投げない
        return None, [UNPARSEABLE_PREFIX + f"予期しない失敗（{type(e).__name__}）"]


def page_eligibility(fm: dict) -> list[str]:
    """蒸留対象になれる page か（契約 §1・§2）。問題を列挙して返す。"""
    problems = []
    if str(fm.get("procedure", "")).strip().lower() != "true":
        problems.append("procedure: true が必要")
    did = fm.get("distill_id")
    if not did or not DISTILL_ID_RE.match(str(did)):
        problems.append("distill_id（d-<8hex>）が必要")
    if str(fm.get("trust", "")).strip() != "trusted":
        problems.append("trust: trusted の明示値が必要（省略は対象外）")
    if not str(fm.get("distill_reviewed_by", "")).strip():
        problems.append("distill_reviewed_by が必要")
    if not str(fm.get("distill_reviewed_at", "")).strip():
        problems.append("distill_reviewed_at が必要")
    return problems


def set_frontmatter_values(text: str, values: dict) -> str:
    """frontmatter の key を更新（無ければ closing `---` の直前へ追加）。本文は触らない。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise DistillError("frontmatter がありません")
    close = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close = i
            break
    if close is None:
        raise DistillError("frontmatter の closing --- がありません")
    remaining = dict(values)
    for i in range(1, close):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):", lines[i])
        if m and m.group(1) in remaining:
            lines[i] = f"{m.group(1)}: {remaining.pop(m.group(1))}"
    for k, v in remaining.items():
        lines.insert(close, f"{k}: {v}")
        close += 1
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


# ---------------------------------------------------------------------------
# event store
# ---------------------------------------------------------------------------

def scan_events(root: Path) -> tuple[list[dict], list[str]]:
    """(valid events, load diagnostics) を返す。

    R1: 読めない / object でない / filename と event_id が食い違う file を**黙って捨てない**。
    状態計算には valid だけを使い、validator は diagnostics も fail として扱う
    （破損や改名を「event の消失」にすると head が巻き戻り、後続操作を許してしまう）。
    """
    d = events_dir(root)
    out, problems = [], []
    if not d.exists():
        return out, problems
    for p in sorted(d.iterdir()):
        if p.is_dir():
            problems.append(f"{p.name}: events/ 直下の directory は許されません")
            continue
        if p.suffix != ".json":
            problems.append(f"{p.name}: events/ には .json だけを置きます")
            continue
        try:
            raw = p.read_bytes()
        except OSError as e:
            problems.append(f"{p.name}: 読めません（{type(e).__name__}）")
            continue
        try:
            ev = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            problems.append(f"{p.name}: JSON として読めません（{type(e).__name__}）")
            continue
        if not isinstance(ev, dict):
            problems.append(f"{p.name}: root が object ではありません")
            continue
        if ev.get("event_id") != p.stem:
            problems.append(f"{p.name}: event_id {ev.get('event_id')!r} が filename と一致しません")
            continue
        ev["_sha256"] = sha256_bytes(raw)
        out.append(ev)
    # V5-R1: 未検証の raw 値を比較しない。sort key は必ず (str, str) へ正規化する
    def _key(e):
        at, eid = e.get("occurred_at"), e.get("event_id")
        return (at if isinstance(at, str) else "", eid if isinstance(eid, str) else "")
    out.sort(key=_key)
    return out, problems


def load_events(root: Path) -> list[dict]:
    """**parse できた** event を返すだけの薄い wrapper（schema/遷移は見ていない）。

    注意: mutating path では使わない。書き込み経路は必ず `assert_store_healthy()` の
    validated events を使うこと（V7-R1: 名前に反して未検証集合であることを明記）。
    """
    return scan_events(root)[0]


SOURCES = ("host-task", "agent-self-report", "human", "system")
STRENGTHS = ("observed", "asserted", "unverifiable")
BLOCK_KINDS = ("input_missing", "precondition_failed", "permission_pending",
               "external_unavailable", "operator_cancelled")
TRIGGER_SOURCES = ("scheduled", "run-now", "explicit-invocation", "manual-procedure")
EVENT_TYPES = ("registered", "observed", "nominated", "decision", "rereviewed",
               "opportunity", "invoked", "completed", "blocked")
SUBJECT_TYPES = ("page", "task", "skill")
# subject_type ごとの必須 / 禁止 field（schema の allOf と同じ規則）
SUBJECT_RULES = {
    "page": (("distill_id", "page_path", "page_sha256"), ("task_id", "skill_slug")),
    "task": (("task_id",), ("distill_id", "page_path", "page_sha256", "skill_slug")),
    "skill": (("skill_slug",), ("task_id", "page_path", "page_sha256")),
}
TOP_LEVEL_FIELDS = {"event_id", "occurred_at", "event_type", "subject", "source", "strength", "actor", "reason",
                    "opportunity_id", "trigger", "block_kind", "previous_event_id", "previous_event_sha256",
                    "expected_previous_state", "new_state", "threshold", "evidence", "bound_to"}
TRIGGER_FIELDS = {"trigger_source", "trigger_ref", "task_metadata_status", "task_metadata",
                  "partial_task_metadata", "unverifiable_reason"}
# event_type ごとの禁止 field（schema の not/anyOf と同じ規則）
FORBIDDEN_BY_TYPE = {
    "registered": ("expected_previous_state", "new_state", "opportunity_id", "trigger", "threshold",
                   "block_kind", "previous_event_id", "previous_event_sha256", "bound_to"),
    "observed": ("opportunity_id", "trigger", "block_kind", "previous_event_id", "previous_event_sha256",
                 "bound_to"),
    "nominated": ("opportunity_id", "trigger", "threshold", "block_kind", "bound_to"),
    "decision": ("opportunity_id", "trigger", "threshold", "block_kind"),
    "rereviewed": ("opportunity_id", "trigger", "threshold", "block_kind"),
    "opportunity": ("expected_previous_state", "new_state", "threshold", "block_kind",
                    "previous_event_id", "previous_event_sha256", "bound_to"),
    "invoked": ("expected_previous_state", "new_state", "trigger", "threshold", "block_kind",
                "previous_event_id", "previous_event_sha256", "bound_to"),
    "completed": ("expected_previous_state", "new_state", "trigger", "threshold", "block_kind",
                  "previous_event_id", "previous_event_sha256", "bound_to"),
    "blocked": ("expected_previous_state", "new_state", "trigger", "threshold",
                "previous_event_id", "previous_event_sha256", "bound_to"),
}


def _is_str(v) -> bool:
    return isinstance(v, str)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and v != ""


def _check_hash_ref(ref, label: str, p: list[str], *, with_path: bool = True) -> None:
    """`{path, sha256}`（または `{sha256}`）の形を total に検査する（型を確かめてから membership）。"""
    if not isinstance(ref, dict):
        p.append(f"{label} が object ではありません")
        return
    allowed = {"path", "sha256"} if with_path else {"sha256"}
    for k in ref:
        if k not in allowed:
            p.append(f"{label} に未知の field: {k}")
    if with_path:
        if "path" not in ref:
            p.append(f"{label}.path が必要です")
        elif not (_is_str(ref["path"]) and PORTABLE_PATH_RE.match(ref["path"])):
            p.append(f"{label}.path が portable path ではありません")
    if "sha256" not in ref:
        p.append(f"{label}.sha256 が必要です")
    elif not (_is_str(ref["sha256"]) and SHA256_RE.match(ref["sha256"])):
        p.append(f"{label}.sha256 の形式が不正です")


def builtin_validate_event(ev) -> list[str]:
    """`distill-event.schema.json` と同等の検証を標準ライブラリだけで行う（V6-R1）。

    jsonschema はあれば **追加の** cross-check として使うが、健全性の判定はこちらが正本。
    これにより「jsonschema が入っていない環境では schema-invalid が healthy になる」fail-open を無くす。
    schema との等価性は tests/test_distill_cli.py の equivalence test が守る。
    """
    p: list[str] = []
    if not isinstance(ev, dict):
        return ["event is not an object"]
    # additionalProperties: false
    for k in ev:
        if k.startswith("_"):
            continue
        if k not in TOP_LEVEL_FIELDS:
            p.append(f"未知の field: {k}")
    for k in ("event_id", "occurred_at", "event_type", "subject", "source", "strength", "actor"):
        if k not in ev:
            p.append(f"必須 field がありません: {k}")
    if "event_id" in ev and not (_is_str(ev["event_id"]) and EVENT_ID_RE.match(ev["event_id"])):
        p.append("event_id の形式が不正です")
    if "occurred_at" in ev and not (_is_str(ev["occurred_at"]) and UTC_RE.match(ev["occurred_at"])):
        p.append("occurred_at の形式が不正です（YYYY-MM-DDTHH:MM:SSZ）")
    et = ev.get("event_type")
    if not _is_str(et) or et not in EVENT_TYPES:
        p.append(f"event_type が不正です: {et!r}")
        et = None
    if "source" in ev and ev["source"] not in SOURCES:
        p.append(f"source が不正です: {ev['source']!r}")
    if "strength" in ev and ev["strength"] not in STRENGTHS:
        p.append(f"strength が不正です: {ev['strength']!r}")
    if "actor" in ev and not _nonempty_str(ev["actor"]):
        p.append("actor は非空文字列である必要があります")
    if "reason" in ev and not _nonempty_str(ev["reason"]):
        p.append("reason は非空文字列である必要があります")
    # subject
    subject = ev.get("subject")
    if not isinstance(subject, dict):
        if "subject" in ev:
            p.append("subject が object ではありません")
        subject = {}
    else:
        st = subject.get("subject_type")
        for k in subject:
            if k not in {"subject_type", "distill_id", "page_path", "page_sha256", "task_id", "skill_slug"}:
                p.append(f"subject に未知の field: {k}")
        if st not in SUBJECT_TYPES:
            p.append(f"subject.subject_type が不正です: {st!r}")
        else:
            required, forbidden = SUBJECT_RULES[st]
            for k in required:
                if k not in subject:
                    p.append(f"subject.{k} が必要です（subject_type={st}）")
            for k in forbidden:
                if k in subject:
                    p.append(f"subject.{k} は subject_type={st} では持てません")
        if "distill_id" in subject and not (_is_str(subject["distill_id"]) and DISTILL_ID_RE.match(subject["distill_id"])):
            p.append("subject.distill_id の形式が不正です")
        if "page_path" in subject and not (_is_str(subject["page_path"]) and PORTABLE_PATH_RE.match(subject["page_path"])):
            p.append("subject.page_path が portable path ではありません")
        if "page_sha256" in subject and not (_is_str(subject["page_sha256"]) and SHA256_RE.match(subject["page_sha256"])):
            p.append("subject.page_sha256 の形式が不正です")
        if "task_id" in subject and not _nonempty_str(subject["task_id"]):
            p.append("subject.task_id は非空文字列である必要があります")
        if "skill_slug" in subject and not (_is_str(subject["skill_slug"]) and SLUG_RE.match(subject["skill_slug"])):
            p.append("subject.skill_slug の形式が不正です")
    # 共通 field の型
    if "opportunity_id" in ev and not (_is_str(ev["opportunity_id"]) and OPPORTUNITY_ID_RE.match(ev["opportunity_id"])):
        p.append("opportunity_id の形式が不正です")
    if "block_kind" in ev and ev["block_kind"] not in BLOCK_KINDS:
        p.append(f"block_kind が不正です: {ev['block_kind']!r}")
    if "previous_event_id" in ev and not (_is_str(ev["previous_event_id"]) and EVENT_ID_RE.match(ev["previous_event_id"])):
        p.append("previous_event_id の形式が不正です")
    if "previous_event_sha256" in ev and not (_is_str(ev["previous_event_sha256"])
                                              and SHA256_RE.match(ev["previous_event_sha256"])):
        p.append("previous_event_sha256 の形式が不正です")
    if "expected_previous_state" in ev and ev["expected_previous_state"] not in CANDIDATE_STATES:
        p.append("expected_previous_state が不正です")
    if "new_state" in ev and ev["new_state"] not in CANDIDATE_STATES[1:]:
        p.append("new_state が不正です")
    # bound_to（merge 3 A: accepted の承認対象を hash で束縛する）。ここでは形だけを total に見る
    if "bound_to" in ev:
        bt = ev["bound_to"]
        if not isinstance(bt, dict):
            p.append("bound_to が object ではありません")
        else:
            for k in bt:
                if k not in BOUND_TO_KEYS:
                    p.append(f"bound_to に未知の field: {k}")
            if "proposal" in bt:
                _check_hash_ref(bt["proposal"], "bound_to.proposal", p)
            if "effect_contract" in bt:
                _check_hash_ref(bt["effect_contract"], "bound_to.effect_contract", p)
            if "candidate_bundle" in bt:
                _check_hash_ref(bt["candidate_bundle"], "bound_to.candidate_bundle", p, with_path=False)
    if "evidence" in ev:
        if not isinstance(ev["evidence"], list):
            p.append("evidence が配列ではありません")
        else:
            for i, ref in enumerate(ev["evidence"]):
                if not isinstance(ref, dict) or set(ref) - {"path", "sha256"} or "path" not in ref or "sha256" not in ref:
                    p.append(f"evidence[{i}] の形が不正です")
                    continue
                if not (_is_str(ref["path"]) and PORTABLE_PATH_RE.match(ref["path"])):
                    p.append(f"evidence[{i}].path が portable path ではありません")
                if not (_is_str(ref["sha256"]) and SHA256_RE.match(ref["sha256"])):
                    p.append(f"evidence[{i}].sha256 の形式が不正です")
    # threshold
    if "threshold" in ev:
        th = ev["threshold"]
        if not isinstance(th, dict):
            p.append("threshold が object ではありません")
        else:
            for k in th:
                if k not in {"window_days", "min_opportunities", "counted_event_ids"}:
                    p.append(f"threshold に未知の field: {k}")
            for k in ("window_days", "min_opportunities"):
                v = th.get(k)
                if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                    p.append(f"threshold.{k} は1以上の整数である必要があります")
            ids = th.get("counted_event_ids")
            if not isinstance(ids, list) or not ids:
                p.append("threshold.counted_event_ids は1件以上の配列である必要があります")
            elif not all(_is_str(x) and EVENT_ID_RE.match(x) for x in ids):
                p.append("threshold.counted_event_ids に不正な event_id があります")
    # trigger
    if "trigger" in ev:
        tr = ev["trigger"]
        if not isinstance(tr, dict):
            p.append("trigger が object ではありません")
        else:
            for k in tr:
                if k not in TRIGGER_FIELDS:
                    p.append(f"trigger に未知の field: {k}")
            for k in ("trigger_source", "trigger_ref", "task_metadata_status"):
                if k not in tr:
                    p.append(f"trigger.{k} が必要です")
            if "trigger_source" in tr and tr["trigger_source"] not in TRIGGER_SOURCES:
                p.append(f"trigger.trigger_source が不正です: {tr['trigger_source']!r}")
            if "trigger_ref" in tr and not _nonempty_str(tr["trigger_ref"]):
                p.append("trigger.trigger_ref は非空文字列である必要があります")
            status = tr.get("task_metadata_status")
            if status not in ("snapshot", "unverifiable"):
                if "task_metadata_status" in tr:
                    p.append(f"trigger.task_metadata_status が不正です: {status!r}")
            elif status == "snapshot":
                if not isinstance(tr.get("task_metadata"), dict) or not tr.get("task_metadata"):
                    p.append("trigger.task_metadata が必要です（status=snapshot）")
                for k in ("unverifiable_reason", "partial_task_metadata"):
                    if k in tr:
                        p.append(f"trigger.{k} は status=snapshot では持てません")
            else:
                if not _nonempty_str(tr.get("unverifiable_reason")):
                    p.append("trigger.unverifiable_reason が必要です（status=unverifiable）")
                if "task_metadata" in tr:
                    p.append("trigger.task_metadata は status=unverifiable では持てません")
            for k in ("task_metadata", "partial_task_metadata"):
                if k in tr and (not isinstance(tr[k], dict) or not tr[k]):
                    p.append(f"trigger.{k} は非空 object である必要があります")
    if et is None:
        return p
    # event_type 別の必須 / 禁止 / source / subject_type
    for k in FORBIDDEN_BY_TYPE.get(et, ()):
        if k in ev:
            p.append(f"{et}: {k} は持てません")
    if et in TRANSITIONS:
        rule = TRANSITIONS[et]
        if ev.get("source") != rule["source"]:
            p.append(f"{et}: source must be {rule['source']}")
        if ev.get("strength") != "observed":
            p.append(f"{et}: strength must be observed")
        if ev.get("expected_previous_state") not in rule["from"]:
            p.append(f"{et}: expected_previous_state must be one of {rule['from']}")
        if ev.get("new_state") not in rule["to"]:
            p.append(f"{et}: new_state must be one of {rule['to']}")
        if subject.get("subject_type") != "page":
            p.append(f"{et}: candidate state events require subject_type=page")
        if et in ("nominated", "decision", "rereviewed") and "reason" not in ev:
            p.append(f"{et}: reason が必要です")
        if et == "observed" and "threshold" not in ev:
            p.append("observed: threshold が必要です")
        if et in ("decision", "rereviewed") and not ("previous_event_id" in ev and "previous_event_sha256" in ev):
            p.append(f"{et}: previous_event_id と previous_event_sha256 が必要です")
        if et == "rereviewed" and ev.get("expected_previous_state") in rule["from"] \
                and ev.get("new_state") != ev.get("expected_previous_state"):
            p.append("rereviewed: state を変えません（new_state は expected_previous_state と同じ値）")
        if et in BINDING_EVENTS:
            # accepted の承認対象（proposal / effect contract）を hash で束縛する。**optional**（R2-1）:
            # merge 3 より前に書かれた accepted event は bound_to を持たない。event は append-only で
            # 直せないので、後から不正にしない（契約「自動失効なし」）。validate が legacy として報告する。
            # accepted 以外に bound_to は付けない（「何を承認したか」の意味が無い束縛を残さない）
            if ev.get("new_state") == "accepted":
                if "bound_to" in ev:
                    bt = ev["bound_to"]
                    if not isinstance(bt, dict):
                        p.append(f"{et}: bound_to が object ではありません")
                    else:
                        for k in ("proposal", "effect_contract"):
                            if k not in bt:
                                p.append(f"{et}: bound_to があるなら bound_to.{k} も必要です")
            elif "bound_to" in ev:
                p.append(f"{et}: bound_to は new_state=accepted のときだけ持てます")
        if et == "nominated":
            if ev.get("expected_previous_state") == "absent":
                for k in ("previous_event_id", "previous_event_sha256"):
                    if k in ev:
                        p.append(f"nominated: absent からの遷移で {k} は持てません")
            elif not ("previous_event_id" in ev and "previous_event_sha256" in ev):
                p.append("nominated: absent 以外は previous_event_id と previous_event_sha256 が必要です")
    elif et == "registered":
        if ev.get("source") != "human":
            p.append("registered: source must be human")
        if ev.get("strength") != "observed":
            p.append("registered: strength must be observed")
        if subject.get("subject_type") != "page":
            p.append("registered: subject_type must be page")
        if "reason" not in ev:
            p.append("registered: reason が必要です")
    else:   # opportunity / invoked / completed / blocked
        if ev.get("source") == "system":
            p.append(f"{et}: source に system は使えません")
        if "opportunity_id" not in ev:
            p.append(f"{et}: opportunity_id が必要です")
        if et == "opportunity" and "trigger" not in ev:
            p.append("opportunity: trigger が必要です")
        if et == "blocked" and "block_kind" not in ev:
            p.append("blocked: block_kind が必要です")
    return p


def validate_event(ev) -> list[str]:
    """event の検証。**built-in validation が正本**で、jsonschema があれば追加の cross-check として併用する。

    どんな JSON 値に対しても例外を出さず problems を返す（total）。jsonschema の有無で
    健全性の判定が変わらない（V6-R1: 不在環境で fail-open にしない）。
    """
    problems = builtin_validate_event(ev)
    if not isinstance(ev, dict):
        return problems
    payload = {k: v for k, v in ev.items() if not k.startswith("_")}   # _sha256 等の内部注釈は除く
    try:
        from jsonschema import Draft202012Validator as V
        schema = json.loads((SCHEMA_DIR / "distill-event.schema.json").read_text(encoding="utf-8"))
        problems += [f"schema: {e.message}" for e in V(schema).iter_errors(payload)]
    except ImportError:
        pass          # built-in が正本なので、不在でも検証は弱まらない
    except (OSError, ValueError) as e:
        problems.append(f"schema file を読めません: {type(e).__name__}")
    return problems


def write_event(root: Path, ev: dict, *, retries: int = 3) -> Path:
    """event を exclusive create で書く（更新禁止）。ID 衝突は乱数を引き直して最大 retries 回。"""
    problems = validate_event(ev)
    if problems:
        raise DistillError("event invalid:\n  " + "\n  ".join(problems))
    d = events_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    last = None
    for _ in range(max(1, retries)):
        p = d / f"{ev['event_id']}.json"
        data = json.dumps({k: v for k, v in ev.items() if not k.startswith("_")},
                          ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as e:
            last = e
            # R5 (G17): id の時刻 prefix は occurred_at と同秒でなければならない（R4-2）。
            # 今の時刻で引き直すと秒境界で食い違うので、token だけを引き直す
            ev = dict(ev, event_id=f"{ev['event_id'][:16]}-{secrets.token_hex(4)}")
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return p
    raise DistillError(f"event id collision after retries: {last}")


def subject_identity(subject: dict | None) -> tuple:
    """event 間で比較する subject の canonical identity（V2-R3）。

    page は (type, distill_id, page_path)、task は (type, task_id)、skill は (type, slug)。
    `page_sha256` は時点ごとに変わるので identity に含めない。
    """
    s = subject or {}
    st = s.get("subject_type")
    if st == "page":
        return ("page", s.get("distill_id"), s.get("page_path"))
    if st == "task":
        return ("task", s.get("task_id"))
    if st == "skill":
        return ("skill", s.get("skill_slug"))
    return ("unknown", json.dumps(s, sort_keys=True, ensure_ascii=False))


def resolved_page(root: Path, rel: str | None) -> Path:
    """event に保存された page_path を **必ず resolver 経由で**開く（R2）。

    nominate 後に配下 directory が symlink / junction へ差し替わっても、
    解決後 path が base 配下でなければここで fail-closed になる。
    """
    if not rel:
        raise DistillError("page_path が空です")
    p = resolve_under_base(root, rel)
    if not p.is_file():
        raise DistillError(f"page が見つからないか file ではありません: {rel}")
    return p


# ---------------------------------------------------------------------------
# proposal（distill/<slug>/proposal.md）— hash 束縛（A）と refs 照合（C）の入力
# ---------------------------------------------------------------------------

# R3-16: bidi 制御（U+202A-202E・U+2066-2069）と行区切り（U+0085・U+2028・U+2029）
_BIDI_CODEPOINTS = tuple(range(0x202a, 0x202f)) + tuple(range(0x2066, 0x206a)) + (0x85, 0x2028, 0x2029)
_BIDI_AND_BREAK_RE = re.compile("[" + "".join(chr(c) for c in _BIDI_CODEPOINTS) + "]")


def _safe(value, limit: int = 160) -> str:
    """入力由来の文字列を stdout へ載せる前の正規化（制御文字除去＋長さ制限）。

    proposal frontmatter・path・role は**データ**であって、報告文の一部として信用しない。
    """
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    # R3-16: bidi 制御と行区切りは「見えないまま表示を組み替える」ので \uXXXX へ逃がす
    s = _BIDI_AND_BREAK_RE.sub(lambda m: "\\u%04x" % ord(m.group(0)), s)
    s = re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", s)
    return s if len(s) <= limit else s[:limit] + "…"


def read_proposal_frontmatter(path: Path, text: str | None = None) -> tuple[dict, list[str]]:
    """proposal.md の frontmatter を読む（total: 例外を出さず (values, problems) を返す）。

    R2-2: 人が書く proposal は普通のブロック形式 YAML なので、`parse_frontmatter_yaml_subset`
    （限定サブセットの total parser）で読む。1行 JSON flow（R1 の書き方）は YAML flow の部分集合として
    そのまま通る。対応しない構文に当たったら **入れ子は何も返さず**、flat 抽出器で読める
    `distill_id` / `skill_slug` だけを identity のために残す（部分的に読めた分を「読めた」と言わない）。

    R3-13: その flat fallback は「最後の同名 key が勝つ」ので、**重複していたら identity にしない**
    （重複 key は subset parser が unparseable にする＝2つの読み方が食い違う入力）。
    `text` を渡すと file を読み直さない（R3-8: 検査と hash を同じ bytes で行う）。
    """
    if text is None:
        text = _read_text_or_empty(path)
    doc, problems = parse_frontmatter_yaml_subset(text)
    if doc is None:
        flat, counts = _flat_frontmatter_with_counts(text)
        return ({k: flat[k] for k in ("distill_id", "skill_slug")
                 if k in flat and counts.get(k) == 1}, problems)
    return doc, problems


def _flat_frontmatter_with_counts(text: str) -> tuple[dict, dict]:
    """flat な `key: value` を、**key ごとの出現数と一緒に**返す（R3-13）。"""
    out, counts = {}, {}
    for line in _frontmatter_lines(text.splitlines()):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if m:
            out[m.group(1)] = yaml_scalar_decode(m.group(2).strip())
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return out, counts


def _read_text_or_empty(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""


def _identifier_text(value):
    """`skill_slug` / `distill_id` / `slug` として読む値を**文字列**として得る（R3-15）。

    `12345678` のような数字だけの slug は parser が int にし得るが、directory 名と比べるのは文字列。
    先頭 0 の数字列と 64桁は parser が文字列のまま残すので、ここで桁は失われない。
    """
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def scan_proposals(root: Path) -> tuple[dict, list[str]]:
    """`distill/<slug>/proposal.md` を走査して distill_id -> record を返す（total）。

    record: `{"slug", "rel", "path", "fm", "unparseable", "sha256"}`。frontmatter の `skill_slug` が
    **directory 名と一致すること**を
    要求する（proposal を別 candidate の hash へ向ける偽装を防ぐ・contract §2 の stable slug）。

    R3-8: file は **1回だけ**読み、その bytes から frontmatter 解析と `sha256` の両方を作る
    （検査した bytes と束縛する hash が同じであることを、読み直さないことで保証する）。
    """
    records, problems = {}, []
    base = distill_dir(root)
    if not base.is_dir():
        return records, problems
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name == "events":
            continue
        if not SLUG_RE.match(child.name):
            continue                          # slug でない directory は proposal 置き場ではない
        rel = f"distill/{child.name}/proposal.md"
        try:
            path = resolve_under_base(root, rel)
        except DistillError as e:
            problems.append(f"{rel}: {_safe(str(e))}")
            continue
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()                       # R3-8: 読むのはここだけ
        except OSError as e:
            problems.append(f"{rel}: 読めません（{type(e).__name__}）")
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            problems.append(f"{rel}: UTF-8 として読めません")
            continue
        fm, fprobs = read_proposal_frontmatter(path, text)
        problems += [f"{rel}: {x}" for x in fprobs]
        unparseable = any(str(x).startswith(UNPARSEABLE_PREFIX) for x in fprobs)
        did, slug = _identifier_text(fm.get("distill_id")), _identifier_text(fm.get("skill_slug"))
        if not (isinstance(did, str) and DISTILL_ID_RE.match(did)):
            problems.append(f"{rel}: frontmatter の distill_id がありません（または形式が不正）")
            continue
        if slug != child.name:
            problems.append(f"{rel}: frontmatter の skill_slug {_safe(slug)} が directory 名 "
                            f"{_safe(child.name)} と一致しません")
            continue
        if did in records:
            problems.append(f"{rel}: distill_id {did} の proposal が複数あります（{records[did]['rel']} と重複）")
            continue
        records[did] = {"slug": child.name, "rel": rel, "path": path, "fm": fm,
                        "unparseable": unparseable, "sha256": sha256_bytes(raw)}
    return records, problems


def find_proposal(root: Path, distill_id: str) -> dict:
    """`distill_id` の proposal record を返す。見つからない／曖昧なら DistillError（何も書かせない）。

    R3-5: **frontmatter が unparseable な proposal は束縛対象にしない**（部分的に読めた identity で
    「承認した」と言わない。仕様 A「frontmatter が壊れている → 何も書かない」）。
    """
    records, problems = scan_proposals(root)
    rec = records.get(distill_id)
    if rec is None:
        detail = ("\n  " + "\n  ".join(problems)) if problems else ""
        raise DistillError(f"{distill_id}: distill/<skill_slug>/proposal.md が見つかりません"
                           f"（accepted は proposal と effect contract の hash に束縛します）{detail}")
    if rec.get("unparseable"):
        raise DistillError(f"{distill_id}: {rec['rel']} の frontmatter に対応しない構文があります"
                           f"（unparseable）。読める形に直してから承認してください")
    return rec


def compute_bound_to(root: Path, distill_id: str, bundle_sha256: str | None = None) -> dict:
    """accepted が束縛する hash 群を作る（契約 §6 の candidate validation ゲート）。

    proposal は **`scan_proposals` が読んだのと同じ bytes** の hash を束縛する（R3-8: 読み直さない。
    検査していない bytes を束縛しない）。effect-contract もここで1回だけ読む。
    どちらかが無い・読めない・frontmatter が壊れているときは DistillError。**書き込みの前に呼ぶこと**。
    """
    if bundle_sha256 is not None and not (isinstance(bundle_sha256, str) and SHA256_RE.match(bundle_sha256)):
        raise DistillError("candidate bundle hash は SHA-256 の 64桁 hex です")
    rec = find_proposal(root, distill_id)
    prop_rel = rec["rel"]
    ec_rel = f"distill/{rec['slug']}/effect-contract.json"
    ec_path = resolve_under_base(root, ec_rel)
    try:
        ec_bytes = ec_path.read_bytes()
    except OSError as e:
        raise DistillError(f"{ec_rel} を読めません（{type(e).__name__}）")
    bound = {"proposal": {"path": prop_rel, "sha256": rec["sha256"]},
             "effect_contract": {"path": ec_rel, "sha256": sha256_bytes(ec_bytes)}}
    if bundle_sha256:
        bound["candidate_bundle"] = {"sha256": bundle_sha256}
    return bound


# ---------------------------------------------------------------------------
# candidate state
# ---------------------------------------------------------------------------

def event_time_problem(ev: dict) -> str | None:
    """`event_id` の時刻 prefix（YYYYMMDDTHHMMSSZ）が `occurred_at` と同じ秒か（R4-2）。

    食い違えば理由文を返す。形式そのものの不正は schema 検査（`store_health`）が報告するので、
    ここでは str でない値には触れない。
    """
    at, eid = ev.get("occurred_at"), ev.get("event_id")
    if not isinstance(at, str) or not isinstance(eid, str):
        return None
    if eid[:16] != at.replace("-", "").replace(":", ""):
        return f"event_id time mismatch（{_safe(eid)} と occurred_at={_safe(at)} が別の秒です）"
    return None


def state_chain(events: list[dict], distill_id: str) -> list[dict]:
    """state-changing event を **previous_event_id の連鎖**で並べる。

    occurred_at は秒精度で、同一秒に書かれた event は file 名（乱数）順になり得る。
    時刻ソートで head を決めると順序が反転し得るため、連鎖を正本にする。
    root 複数・分岐・循環・孤児は DistillError（validate が詳細を報告する）。
    """
    evs = [e for e in events
           if e.get("event_type") in STATE_EVENTS and (e.get("subject") or {}).get("distill_id") == distill_id]
    if not evs:
        return []
    roots = [e for e in evs if not e.get("previous_event_id")]
    children = {}
    for e in evs:
        prev = e.get("previous_event_id")
        if prev:
            children.setdefault(prev, []).append(e)
    if len(roots) != 1:
        raise DistillError(f"{distill_id}: state chain の root が {len(roots)} 件（1件であるべき）")
    chain = [roots[0]]
    seen = {roots[0]["event_id"]}
    while True:
        nxt = children.get(chain[-1]["event_id"], [])
        if not nxt:
            break
        if len(nxt) > 1:
            raise DistillError(f"{distill_id}: state chain が分岐しています（{[e['event_id'] for e in nxt]}）")
        if nxt[0]["event_id"] in seen:
            raise DistillError(f"{distill_id}: state chain に循環があります")
        seen.add(nxt[0]["event_id"])
        chain.append(nxt[0])
    if len(chain) != len(evs):
        orphan = [e["event_id"] for e in evs if e["event_id"] not in seen]
        raise DistillError(f"{distill_id}: 連鎖に繋がらない state event があります（{orphan}）")
    # R4-2: `occurred_at` は攻撃者が書ける（legacy 判定の材料でもある）。chain 上では時刻が
    # **非減少**で、`event_id` の時刻 prefix が `occurred_at` と同じ秒であることを要求する
    # ——今日の nominated の後ろに backdate した accepted を繋いで legacy に化けさせないため
    for ev in chain:
        problem = event_time_problem(ev)
        if problem:
            raise DistillError(f"{distill_id}: {problem}")
    for prev, cur in zip(chain, chain[1:]):
        if str(cur.get("occurred_at")) < str(prev.get("occurred_at")):
            raise DistillError(f"{distill_id}: occurred_at goes backwards"
                               f"（{cur['event_id']}: {_safe(prev.get('occurred_at'))} -> "
                               f"{_safe(cur.get('occurred_at'))}）")
    # R3-3: candidate identity は (distill_id, page_path) の対。chain の途中で page_path が変わったら
    # 「同じ candidate」ではない（page の移動・改名は現在の契約に無い。専用 event を設計するまで拒む）
    for prev, cur in zip(chain, chain[1:]):
        before = (prev.get("subject") or {}).get("page_path")
        after = (cur.get("subject") or {}).get("page_path")
        if path_identity(before) != path_identity(after):
            raise DistillError(f"{distill_id}: subject identity changed"
                               f"（{cur['event_id']}: page_path {_safe(before)} -> {_safe(after)}）")
    return chain


def state_head(events: list[dict], distill_id: str) -> tuple[str, dict | None]:
    """(現在の state, chain の末尾 event) を返す。event が無ければ ("absent", None)。"""
    chain = state_chain(events, distill_id)
    return (chain[-1]["new_state"], chain[-1]) if chain else ("absent", None)


def candidate_states(events: list[dict]) -> dict:
    """distill_id -> {state, head_event_id, page_path, updated} の一覧（chain 基準）。"""
    dids = {(ev.get("subject") or {}).get("distill_id") for ev in events}
    out = {}
    for did in sorted(x for x in dids if x):
        page_path = None
        for ev in events:
            subj = ev.get("subject") or {}
            if subj.get("distill_id") == did and subj.get("page_path"):
                page_path = subj["page_path"]
        try:
            chain = state_chain(events, did)
        except DistillError as e:
            out[did] = {"state": "ambiguous", "head_event_id": None, "page_path": page_path,
                        "updated": None, "error": str(e)}
            continue
        # R4-3: page identity は **state chain の head**（人が review して束縛した時点）から採る。
        # 非 state event（opportunity 等）の subject に引きずられると page drift 検査が逸れる。
        # chain が無い candidate（opportunity だけの観測段階）は従来どおり最後の subject を使う
        head_subj = (chain[-1].get("subject") or {}) if chain else {}
        if head_subj.get("page_path"):
            page_path = head_subj["page_path"]
        out[did] = {"state": chain[-1]["new_state"] if chain else "absent",
                    "head_event_id": chain[-1]["event_id"] if chain else None,
                    # R3-4: head event は誰の previous_event_sha256 にも参照されない（錨が無い）。
                    # index に head の sha を持たせ、「書き換えたが reindex を忘れた」を検出できるようにする
                    "head_sha256": chain[-1].get("_sha256") if chain else None,
                    "page_path": page_path,
                    "page_sha256": head_subj.get("page_sha256"),
                    "updated": chain[-1].get("occurred_at") if chain else None}
    return out



def _align_clock(head: dict | None, ev: dict, *, max_skew_s: int = 300) -> dict:
    """R5 (G18): 新しい event の時刻が chain head より前なら、小さなずれは head に揃え、大きければ拒む。

    R4-2 の時刻単調性は validate が検査する。時計が数秒進んだ host で 1 event 書いたあと正しい
    時計で書くと、書けてしまってから store が恒久 FAIL になる（append-only なので CLI で直せない）。
    そこで書く前に head と比べ、ずれが max_skew_s 以内なら **occurred_at と event_id の時刻 prefix を
    head と同じ秒に揃える**（「head より前ではない」という事実だけを記録する。token は保つ）。
    ずれが大きいときは時計の異常なので拒む。揃えた事実は理由で偽らない（時刻を進めただけ）。
    """
    if not head:
        return ev
    prev = head.get("occurred_at")
    now = ev.get("occurred_at")
    if not (isinstance(prev, str) and isinstance(now, str)) or now >= prev:
        return ev
    try:
        dt_prev = datetime.datetime.strptime(prev, "%Y-%m-%dT%H:%M:%SZ")
        dt_now = datetime.datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise DistillError("clock regression: chain head の occurred_at が読めません") from None
    skew = int((dt_prev - dt_now).total_seconds())
    if skew > max_skew_s:
        raise DistillError(
            f"clock regression: new event {now} is {skew}s earlier than chain head {prev}"
            f"（{head.get('event_id')}）。時計を確認してください")
    token = ev["event_id"].rsplit("-", 1)[-1]
    return dict(ev, occurred_at=prev, event_id=f"{dt_prev.strftime('%Y%m%dT%H%M%SZ')}-{token}")


def _base_event(event_type: str, subject: dict, source: str, actor: str, *, strength: str = "observed",
                reason: str | None = None) -> dict:
    # R4-2: event_id の時刻 prefix と occurred_at は**同じ秒**であること（validate が検査する）。
    # 別々に採ると秒境界をまたいで食い違い得るので、1つの時刻から両方を作る
    ts = datetime.datetime.now(datetime.timezone.utc)
    ev = {"event_id": f"{ts.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}",
          "occurred_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "event_type": event_type,
          "subject": subject, "source": source, "strength": strength, "actor": actor}
    if reason:
        ev["reason"] = reason
    return ev


def _actor() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


# ---------------------------------------------------------------------------
# 派生 index（C-03: 再生成のみが書く）
# ---------------------------------------------------------------------------

def render_index(root: Path, events: list[dict] | None = None) -> str:
    events = load_events(root) if events is None else events
    states = candidate_states(events)
    opp = [e for e in events if e.get("event_type") == "opportunity"]
    lines = ["# distill index", "",
             "> 派生物。`distill reindex` だけが書く（手書き禁止）。正本は `distill/events/` と manifest。", "",
             f"- events: {len(events)}", f"- candidates: {len(states)}", f"- opportunities: {len(opp)}", ""]
    lines.append("| distill_id | state | page | head event | head sha256 | updated |")
    lines.append("|---|---|---|---|---|---|")
    for did in sorted(states):
        s = states[did]
        head_sha = s.get("head_sha256")
        lines.append(f"| {did} | {s['state']} | {_safe(s.get('page_path') or '-', 512)} | "
                     f"{s.get('head_event_id') or '-'} | {head_sha or '-'} | "
                     f"{s.get('updated') or '-'} |")
    lines.append("")
    return "\n".join(lines)


def reindex_locked(root: Path, events: list[dict] | None = None) -> Path:
    """**lock 保持済み**が前提の内部 helper（R3）。単体で呼ばない。"""
    p = distill_dir(root) / "_index.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, render_index(root, events))
    return p


def cmd_reindex(root: Path) -> Path:
    """CLI verb。lock を取り、**store health を確認してから**再生成する（V3-R2）。

    破損 store の valid subset で派生物を作ると、壊れた状態を「正常な index」として固定してしまう。
    """
    with VaultLock(root):
        events = assert_store_healthy(root)
        return reindex_locked(root, events)


# ---------------------------------------------------------------------------
# 閾値（静かな候補発見・C-05: false negative 許容）
# ---------------------------------------------------------------------------

def opportunity_counts(events: list[dict], *, window_days: int = DEFAULT_WINDOW_DAYS,
                       now: datetime.datetime | None = None) -> dict:
    """distill_id -> 窓内の算入可能 opportunity event id 一覧（dedupe 済み）。"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=window_days)
    seen, out = set(), {}
    for ev in events:
        if ev.get("event_type") != "opportunity" or ev.get("strength") not in COUNTED_STRENGTHS:
            continue
        did = (ev.get("subject") or {}).get("distill_id")
        if not did:
            continue
        try:
            at = datetime.datetime.strptime(ev["occurred_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc)
        except (KeyError, ValueError):
            continue
        if at < start or at > now:      # R6: 窓は下限も上限も閉じる（未来時刻の event は算入しない）
            continue
        trig = ev.get("trigger") or {}
        key = (did, trig.get("trigger_source"), trig.get("trigger_ref"))
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(did, []).append(ev["event_id"])
    return out


def store_health(root: Path) -> tuple[list[dict], list[str]]:
    """store health の**単一の定義**（V3-R1）: load diagnostics ＋ 全 event の schema / 遷移検証。

    「JSON として読めて filename が一致する」だけでは健全ではない。schema や遷移表に反する event が
    1件でもあれば、その store の上に新しい event を積んではいけないし、派生物も作り直さない。
    """
    events, problems = scan_events(root)
    valid = []
    for ev in events:
        eprobs = validate_event(ev)
        problems += [f"{ev.get('event_id')}: {p}" for p in eprobs]
        if not eprobs:
            valid.append(ev)          # V4-R1: 状態計算へ渡すのは検証を通ったものだけ
    return valid, problems


def assert_store_healthy(root: Path) -> list[dict]:
    """**lock 内で**呼ぶ。健全でなければ書き込みを拒否し、健全なら validated events を返す。

    破損や不正を放置したまま書くと、巻き戻った head の上に新しい event を積むことになり、
    immutable store へ回復困難な不整合を足してしまう。
    """
    events, problems = store_health(root)
    if problems:
        raise DistillError("event store が健全ではありません（先に修復してください）:\n  " + "\n  ".join(problems))
    return events


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_nominate(root: Path, page: str, reason: str, actor: str | None = None) -> int:
    """人の直接指名: frontmatter 付与＋`registered`＋`nominated` を同一 lock 内で commit（C-01）。"""
    actor = actor or _actor()
    page_path = resolve_under_base(root, page)
    if not page_path.is_file():
        raise DistillError(f"page not found: {page}")
    rel = page_path.resolve().relative_to(Path(root).resolve()).as_posix()
    with VaultLock(root):
        events = assert_store_healthy(root)   # V2-R1: frontmatter を触る前に store の健全性を確認する
        fm = read_frontmatter(page_path)
        # review 済みであることは人が付ける（自動付与しない）
        for k in ("trust", "distill_reviewed_by", "distill_reviewed_at"):
            if not str(fm.get(k, "")).strip():
                raise DistillError(f"{rel}: {k} が未設定です（蒸留候補は review 済みページのみ・契約 §1）")
        if str(fm.get("trust")).strip() != "trusted":
            raise DistillError(f"{rel}: trust は明示的に trusted である必要があります")
        did = fm.get("distill_id")
        text = page_path.read_text(encoding="utf-8")
        newly_registered = False
        if not did or not DISTILL_ID_RE.match(str(did)):
            did = new_distill_id()
            updates = {"distill_id": did}
            if str(fm.get("procedure", "")).strip().lower() != "true":
                updates["procedure"] = "true"
            if "distilled_to" not in fm:
                updates["distilled_to"] = "[]"
            text = set_frontmatter_values(text, updates)
            atomic_write_text(page_path, text)
            newly_registered = True
        problems = page_eligibility(read_frontmatter(page_path))
        if problems:
            raise DistillError(f"{rel}: 蒸留対象の要件を満たしません:\n  " + "\n  ".join(problems))
        # R3-3: distill_id は Vault 内で unique。既に別 page に state event があるなら、その id は
        # その page のもの（frontmatter を複製した page から identity を乗っ取れないようにする・契約 §2）
        for ev0 in events:
            subj0 = ev0.get("subject") or {}
            if subj0.get("distill_id") != did or not subj0.get("page_path"):
                continue
            if path_identity(subj0["page_path"]) != path_identity(rel):
                raise DistillError(
                    f"{did} は既に {_safe(subj0['page_path'])} の candidate です"
                    f"（distill_id は Vault 内で一意。{_safe(rel)} には新しい id を振ってください）")
        subject = {"subject_type": "page", "distill_id": did, "page_path": rel,
                   "page_sha256": sha256_file(page_path)}
        state, head = state_head(events, did)
        if state == "nominated":
            raise DistillError(f"{did} は既に nominated です")
        if state in ("accepted", "rejected"):
            raise DistillError(f"{did} は {state}（terminal）です")
        written = []
        if newly_registered:
            written.append(write_event(root, _base_event("registered", subject, "human", actor,
                                                         reason=reason or "distill_id registration")))
        ev = _base_event("nominated", subject, "human", actor, reason=reason)
        ev = _align_clock(head, ev)
        ev.update(expected_previous_state=state, new_state="nominated")
        if state != "absent":
            if head is None:
                raise DistillError("head event が見つかりません（state 不整合）")
            ev.update(previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
        written.append(write_event(root, ev))
        reindex_locked(root)
    print(f"NOMINATED {did} ({rel})")
    for p in written:
        print(f"  event: distill/events/{p.name}")
    return 0


def cmd_decide(root: Path, distill_id: str, new_state: str, reason: str, actor: str | None = None,
               bundle_sha256: str | None = None) -> int:
    """nominated → held|rejected|accepted。`accepted` は proposal / effect contract の hash を自動で束縛する。

    束縛の計算（proposal の探索・読み取り・hash）は **event を書く前に**終わらせる。失敗したら何も書かない
    ——hash 無しの accepted を出せる経路を残さない（契約 §6）。
    """
    actor = actor or _actor()
    if new_state not in ("held", "rejected", "accepted"):
        raise DistillError("decide の new_state は held|rejected|accepted")
    if not reason.strip():
        raise DistillError("--reason は必須です（decision は理由と共に記録する）")
    if bundle_sha256 and new_state != "accepted":
        raise DistillError("candidate bundle hash は accepted のときだけ束縛できます")
    with VaultLock(root):
        events = assert_store_healthy(root)
        state, head = state_head(events, distill_id)
        if state != "nominated":
            raise DistillError(f"{distill_id} の state は {state}（decision は nominated からのみ）")
        if head is None:
            raise DistillError("head event が見つかりません")
        subject = dict(head["subject"])
        page = resolved_page(root, subject.get("page_path"))   # R2: resolver 経由
        subject["page_sha256"] = sha256_file(page)             # 決定時点の内容を束縛し直す
        bound = compute_bound_to(root, distill_id, bundle_sha256) if new_state == "accepted" else None
        ev = _base_event("decision", subject, "human", actor, reason=reason)
        ev = _align_clock(head, ev)
        ev.update(expected_previous_state=state, new_state=new_state,
                  previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
        if bound is not None:
            ev["bound_to"] = bound
        p = write_event(root, ev)
        reindex_locked(root)
    print(f"DECIDED {distill_id}: {state} -> {new_state}\n  event: distill/events/{p.name}")
    if bound is not None:
        print(f"  bound_to: proposal={bound['proposal']['sha256'][:12]}… "
              f"effect_contract={bound['effect_contract']['sha256'][:12]}…"
              + (f" candidate_bundle={bound['candidate_bundle']['sha256'][:12]}…" if "candidate_bundle" in bound else ""))
    return 0


def cmd_rereview(root: Path, distill_id: str, reason: str, actor: str | None = None,
                 bundle_sha256: str | None = None, drop_bundle: bool = False) -> int:
    """人が再レビューした結果を1イベントで記録する（`rereviewed`・state は変えない）。

    承認済み／指名済みのページを直すと page drift で fail-closed になる。人がやったのは**1回のレビュー**なので、
    記録も1つにする（`decide held` → `nominate` の2発で代用しない）。subject の `page_sha256` を現在の実体で
    束縛し直し、`accepted` からの再レビューでは `bound_to` も現在の proposal / effect contract で作り直す。
    **人の明示指示でしか出さない event**（自動化しない）。

    R3-10: `--bundle-sha256` を省いたときは**直前の accepted の `candidate_bundle` を引き継ぐ**
    （再レビューで束縛が黙って弱まらないように）。外すときは `drop_bundle=True` を明示する。
    """
    actor = actor or _actor()
    if not reason.strip():
        raise DistillError("--reason は必須です（再レビューは理由と共に記録する）")
    if bundle_sha256 and drop_bundle:
        raise DistillError("--bundle-sha256 と --drop-bundle は同時に使えません")
    with VaultLock(root):
        events = assert_store_healthy(root)
        state, head = state_head(events, distill_id)
        if state not in TRANSITIONS["rereviewed"]["from"]:
            raise DistillError(f"{distill_id} の state は {state}"
                               f"（rereview は {' / '.join(TRANSITIONS['rereviewed']['from'])} からのみ）")
        if head is None:
            raise DistillError("head event が見つかりません")
        if bundle_sha256 and state != "accepted":
            raise DistillError("candidate bundle hash は accepted の再レビューでだけ束縛できます")
        subject = dict(head["subject"])
        page = resolved_page(root, subject.get("page_path"))   # 読めなければここで止まる（何も書かない）
        subject["page_sha256"] = sha256_file(page)
        if state == "accepted" and not bundle_sha256 and not drop_bundle:
            prev = (head.get("bound_to") or {}).get("candidate_bundle")
            inherited = prev.get("sha256") if isinstance(prev, dict) else None
            if isinstance(inherited, str) and SHA256_RE.match(inherited):
                bundle_sha256 = inherited                      # R3-10: 黙って外さない
        bound = compute_bound_to(root, distill_id, bundle_sha256) if state == "accepted" else None
        ev = _base_event("rereviewed", subject, "human", actor, reason=reason)
        ev = _align_clock(head, ev)
        ev.update(expected_previous_state=state, new_state=state,
                  previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
        if bound is not None:
            ev["bound_to"] = bound
        p = write_event(root, ev)
        reindex_locked(root)
    print(f"REREVIEWED {distill_id}: state={state}（変更なし）\n  event: distill/events/{p.name}")
    print(f"  page_sha256: {subject['page_sha256'][:12]}…")
    if bound is not None:
        print(f"  bound_to: proposal={bound['proposal']['sha256'][:12]}… "
              f"effect_contract={bound['effect_contract']['sha256'][:12]}…")
    return 0


def cmd_note(root: Path, event_type: str, *, distill_id: str | None, task_id: str | None,
             trigger_source: str, trigger_ref: str | None, opportunity_id: str | None,
             block_kind: str | None, source: str, strength: str, reason: str | None,
             task_metadata: dict | None, unverifiable_reason: str | None, actor: str | None = None,
             host: str | None = None) -> int:
    """opportunity / invoked / completed / blocked を記録する（候補発見用・安全 gate ではない）。

    V7-R1: store の読み取り・subject 解決・page hash・先行 opportunity 検査・書き込みは
    **すべて同一 VaultLock 内**で、`assert_store_healthy` が返した validated events に対して行う。
    health gate より前に raw store を読むと、schema-invalid event で例外終了したり、
    検査に使った snapshot と event 構築に使った snapshot がずれたりする。
    """
    actor = actor or _actor()
    if event_type not in OPPORTUNITY_EVENTS:
        raise DistillError("note は opportunity|invoked|completed|blocked のみ")
    if not distill_id and not task_id:
        raise DistillError("--distill-id か --task-id のどちらかが必要です")
    if event_type != "opportunity" and not opportunity_id:
        raise DistillError(f"{event_type} には --opportunity-id が必要です（先行 opportunity を参照）")
    if event_type == "blocked" and not block_kind:
        raise DistillError("blocked には --block-kind が必要です")

    with VaultLock(root):
        events = assert_store_healthy(root)      # ここより前に store を読まない
        if distill_id:
            subj = None
            for ev0 in reversed(events):         # validated events だけを走査する
                s = ev0.get("subject") or {}
                if s.get("subject_type") == "page" and s.get("distill_id") == distill_id:
                    subj = dict(s)
                    break
            if subj is None:
                raise DistillError(f"unknown distill_id: {distill_id}（先に nominate してください）")
            page = resolved_page(root, subj.get("page_path"))      # R2: resolver 経由
            subj["page_sha256"] = sha256_file(page)
            subject = subj
        else:
            subject = {"subject_type": "task", "task_id": task_id}

        ev = _base_event(event_type, subject, source, actor, strength=strength, reason=reason)
        if event_type == "opportunity":
            # R6: dedupe key の host は evidence の source enum ではなく **host identity**
            ref = trigger_ref or derive_trigger_ref(task_id or (subject.get("page_path") or ""),
                                                    now_utc(), host or host_identity())
            trig = {"trigger_source": trigger_source, "trigger_ref": ref,
                    "task_metadata_status": "snapshot" if task_metadata else "unverifiable"}
            if task_metadata:
                trig["task_metadata"] = task_metadata
            else:
                trig["unverifiable_reason"] = unverifiable_reason or "no host adapter for task metadata"
            ev.update(opportunity_id=opportunity_id or new_opportunity_id(), trigger=trig)
            if any(e.get("opportunity_id") == ev["opportunity_id"] and e.get("event_type") == "opportunity"
                   for e in events):
                raise DistillError(f"opportunity_id が既に存在します: {ev['opportunity_id']}（V2-R4: 全体で一意）")
        else:
            ev["opportunity_id"] = opportunity_id
            if event_type == "blocked":
                ev["block_kind"] = block_kind
            opp = next((e for e in events
                        if e.get("event_type") == "opportunity" and e.get("opportunity_id") == opportunity_id), None)
            if opp is None:
                raise DistillError(f"先行 opportunity が見つかりません: {opportunity_id}")
            if subject_identity(opp.get("subject")) != subject_identity(subject):
                raise DistillError(f"先行 opportunity と subject が一致しません: "
                                   f"{subject_identity(opp.get('subject'))} != {subject_identity(subject)}")
            if event_type in TERMINAL_EVENTS:
                existing = [e["event_type"] for e in events
                            if e.get("event_type") in TERMINAL_EVENTS and e.get("opportunity_id") == opportunity_id]
                if existing:
                    raise DistillError(f"opportunity {opportunity_id} は既に {existing[0]} です"
                                       f"（1 opportunity に terminal は1つ）")
        p = write_event(root, ev)
        reindex_locked(root)
    print(f"NOTED {event_type}: distill/events/{p.name}"
          + (f" (opportunity {ev.get('opportunity_id')})" if ev.get("opportunity_id") else ""))
    return 0


def host_identity() -> str:
    """dedupe key に使う host 識別子（COMPUTERNAME / HOSTNAME、無ければ platform.node()）。"""
    import platform
    return (os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME")
            or platform.node() or "unknown-host")


def derive_trigger_ref(task_id: str, fire_time_utc: str, host: str) -> str:
    """host が run ID を出さないときの決定論的 trigger_ref（契約 §3）。分精度で丸める。"""
    minute = fire_time_utc[:16]  # YYYY-MM-DDTHH:MM
    return sha256_bytes(f"{task_id}|{minute}|{host}".encode("utf-8"))[:16]


def cmd_status(root: Path, distill_id: str | None = None, window_days: int = DEFAULT_WINDOW_DAYS,
               min_opportunities: int = DEFAULT_MIN_OPPORTUNITIES) -> int:
    """read-only。state を変えない。store が壊れていれば警告し rc=2（巻き戻った state を正常に見せない）。"""
    # V4-R1: 計算に使うのは validated events だけ。診断があれば表示して rc=2（不正 event を state へ混ぜない）
    events, load_problems = store_health(root)
    if load_problems:
        print("WARNING: event store に問題があります（表示中の state は検証を通った event だけで計算しています）:",
              file=sys.stderr)
        for p in load_problems:
            print(f"  {p}", file=sys.stderr)
    states = candidate_states(events)
    counts = opportunity_counts(events, window_days=window_days)
    if distill_id:
        if distill_id not in states:
            print(f"unknown distill_id: {distill_id}")
            return 2 if load_problems else 1
        s = states[distill_id]
        print(f"{distill_id}: state={s['state']} page={s.get('page_path')} head={s.get('head_event_id')}")
        print(f"  opportunities({window_days}d, counted): {len(counts.get(distill_id, []))}")
        for ev in events:
            if (ev.get("subject") or {}).get("distill_id") == distill_id:
                print(f"  {ev['occurred_at']} {ev['event_type']:11s} {ev.get('new_state') or ''}")
        return 2 if load_problems else 0
    print(f"events: {len(events)} | candidates: {len(states)}")
    for did in sorted(states):
        s = states[did]
        n = len(counts.get(did, []))
        flag = "  <- 閾値到達（observed 化の候補）" if (s["state"] == "absent" and n >= min_opportunities) else ""
        print(f"  {did}  {s['state']:9s}  opp({window_days}d)={n}  {s.get('page_path') or '-'}{flag}")
    ready = [d for d, ids in counts.items() if len(ids) >= min_opportunities and states.get(d, {}).get("state") == "absent"]
    print(f"蒸留候補: {len(ready)}件")
    return 2 if load_problems else 0


def check_bound_drift(root: Path, events: list[dict], states: dict,
                      records: dict | None = None) -> tuple[list[str], list[str]]:
    """accepted の head が束縛した proposal / effect contract の hash を**現在の実体**と照合する（A）。

    返り値 `(problems, legacy)`。page drift と同じ扱い: state は書き換えない（自動失効なし）が、
    再レビューまで fail-closed（契約 §7）。`records`（`scan_proposals` の結果）を渡すと、束縛先の
    proposal が**その candidate のもの**かも確かめる（CLI を迂回して別 candidate の hash を書いた
    event を「一致」と見せない）。

    R2-1 / R3-9: `bound_to` を持たない accepted は、**`MERGE3_CUTOFF` より前**に書かれたものだけ
    `legacy` として別に返す（FAIL にしない。append-only の過去 event を後から不正にしない）。
    cutoff 以降の bound_to 無し accepted は FAIL——新しい CLI は必ず束縛を書くので、無いのは迂回の印。
    束縛し直す正規の経路は `distill rereview`（人の明示指示）。

    R3-1 / R3-2: 束縛先の path が**その candidate 自身**の proposal / effect contract であることを
    要求する（hash が実体と一致するだけでは「何を承認したか」を保証しない）。比較は
    `normalize_portable` を通した**文字列の等値**で、「解決すると同じ file に着く」を根拠にしない。
    """
    problems, legacy = [], []
    for did in sorted(states):
        if states[did].get("state") != "accepted":
            continue
        try:
            chain = state_chain(events, did)
        except DistillError:
            continue                        # chain の異常は呼び出し側で報告済み
        head = chain[-1] if chain else None
        if "bound_to" not in (head or {}):
            head_id = (head or {}).get("event_id")
            # id と file bytes の sha256 が両方一致するときだけ legacy（R5/G19）
            if LEGACY_ACCEPTED_EVENTS.get(head_id) == (head or {}).get("_sha256"):
                legacy.append(f"{did}: accepted に bound_to がありません"
                              f"（merge 3 より前の既知の event: {head_id}）。"
                              f"`distill rereview {did}` で束縛し直せます")
            else:
                problems.append(f"{did}: accepted に bound_to がありません"
                                f"（{head_id} は merge 3 導入後の event か、既知の legacy に無い event です。"
                                f"CLI は必ず束縛を書きます）")
            continue
        bound = head.get("bound_to")
        if not isinstance(bound, dict):
            problems.append(f"{did}: bound_to が object ではありません")
            continue
        # R3-1: 束縛先は**自分の** proposal / effect contract でなければならない
        rec = (records or {}).get(did)
        if rec is None:
            problems.append(f"{did}: bound_to の束縛先を確認できません"
                            f"（この candidate の proposal が読めません）")
        expected_paths = ({"proposal": rec["rel"],
                           "effect_contract": f"distill/{rec['slug']}/effect-contract.json"}
                          if rec else {})
        for key, label in (("proposal", "proposal drift"), ("effect_contract", "effect-contract drift")):
            ref = bound.get(key)
            if not isinstance(ref, dict):
                problems.append(f"{did}: bound_to.{key} がありません")
                continue
            rel, expected = ref.get("path"), ref.get("sha256")
            want = expected_paths.get(key)
            if want is not None and path_identity(rel) != path_identity(want):
                problems.append(f"{did}: bound_to points outside its own candidate"
                                f"（bound_to.{key} = {_safe(rel)}）。この candidate の {key} は "
                                f"{_safe(want)} です")
                continue
            try:
                path = resolve_under_base(root, rel)
            except DistillError as e:
                problems.append(f"{did}: {label}（{_safe(str(e))}）")
                continue
            try:
                current = sha256_file(path)
            except OSError:
                problems.append(f"{did}: {label}（{_safe(rel)} を読めません）")
                continue
            if not path.is_file():
                problems.append(f"{did}: {label}（{_safe(rel)} がありません）")
                continue
            if current != expected:
                problems.append(f"{did}: {label}（{_safe(rel)} は承認時から変わっています: "
                                f"{str(expected)[:12]}… -> {current[:12]}…）。"
                                f"人が再レビューして `distill rereview {did}` で束縛し直してください")
    return problems, legacy


def check_proposal_documents(root: Path, records: dict) -> list[str]:
    """D / E の後方互換 field と top-level field の食い違いを報告する（top-level が正）。"""
    problems = []
    for did in sorted(records):
        rec = records[did]
        fm = rec["fm"]
        ext = fm.get("extensions")
        rev = fm.get("document_revision")
        ext_rev = ext.get("revision") if isinstance(ext, dict) else None
        # R3-11: 実物の proposal は `extensions.revision` を mapping にしている。dict をそのまま
        # 比較（str(dict)）すると常に食い違い扱いになり、理由文にも dict が丸ごと載る。
        # mapping なら中の document_revision を比較相手にする
        if isinstance(ext_rev, dict):
            ext_rev = ext_rev.get("document_revision")
        # 版番号は**文字列として**比べる（parser が `0.1` / `0.10` を float にしないので別物のまま）
        if isinstance(rev, str) and isinstance(ext_rev, str) and rev != ext_rev:
            problems.append(f"{rec['rel']}: document_revision={_safe(rev)} と extensions.revision="
                            f"{_safe(ext_rev)} が食い違います（top-level を正とします）")
        elif rev is not None and ext_rev is not None and not (isinstance(rev, str) and isinstance(ext_rev, str)):
            problems.append(f"{rec['rel']}: document_revision / extensions.revision は文字列で書いてください"
                            f"（比較できない型です）")
        ec_rel = f"distill/{rec['slug']}/effect-contract.json"
        try:
            ec_path = resolve_under_base(root, ec_rel)
            contract = json.loads(ec_path.read_bytes().decode("utf-8")) if ec_path.is_file() else None
        except (DistillError, OSError, UnicodeDecodeError, ValueError):
            problems.append(f"{ec_rel}: JSON として読めません")
            continue
        if not isinstance(contract, dict):
            if contract is not None:
                problems.append(f"{ec_rel}: root が object ではありません")
            continue
        inputs, cext = contract.get("expected_inputs"), contract.get("extensions")
        legacy = cext.get("expected-accounts") if isinstance(cext, dict) else None
        accounts = inputs.get("accounts") if isinstance(inputs, dict) else None
        if accounts is not None and legacy is not None and accounts != legacy:
            problems.append(f"{ec_rel}: expected_inputs.accounts と extensions.expected-accounts が食い違います"
                            f"（top-level を正とします）")
    return problems


def resolve_ref_path(root: Path, path, ref_bases: dict | None):
    """ref の path を実 path へ解決する。`("resolved", Path, "")` か `("unverifiable", None, 理由)`。

    解決規則を暗黙にしない: `wiki/…` / `distill/…` は Vault root 配下、それ以外は先頭 segment を base id と
    みなし `--ref-base <id>=<abs_dir>` で与えられた base 配下へ解決する。base が無い・containment 違反は
    **unverifiable**（「検証していない」を「一致」と混ぜない）。
    """
    if not (isinstance(path, str) and PORTABLE_PATH_RE.match(path)):
        return "unverifiable", None, f"portable path ではありません: {_safe(path)}"
    first = path.split("/")[0]
    if first in VAULT_REF_ROOTS:
        base, rel = Path(root), path
    else:
        base_dir = (ref_bases or {}).get(first)
        if base_dir is None:
            return "unverifiable", None, f"base id '{_safe(first)}' が --ref-base で与えられていません"
        rest = path[len(first) + 1:]
        if not rest:
            return "unverifiable", None, "base id だけの path です"
        base = Path(base_dir)
        if not base.is_dir():
            return "unverifiable", None, f"--ref-base '{_safe(first)}' の directory がありません"
        rel = rest
    try:
        return "resolved", resolve_under_base(base, rel), ""
    except DistillError as e:
        return "unverifiable", None, f"base 配下に解決できません（{_safe(str(e))}）"


def check_refs(root: Path, records: dict, ref_bases: dict | None):
    """proposal の `source_refs[]` と `effect_contract` を実体 hash と照合する（C）。

    返り値 `(problems, counts, lines)`。`ok` / `mismatch`（FAIL）/ `unverifiable`（報告のみ）の3値で数える。
    role は**報告に載せるだけ**（source ref の mismatch は「再レビューが要る」の意味であり、
    ここは実行前 fail-close の gate ではない）。
    """
    counts = {"ok": 0, "mismatch": 0, "unverifiable": 0}
    by_role: dict = {}
    problems, lines = [], []

    def record(status, did, role, path, note=""):
        counts[status] += 1
        by_role.setdefault(_safe(role, 40), {"ok": 0, "mismatch": 0, "unverifiable": 0})[status] += 1
        # 注: 出力は cp932 端末でも落ちない文字だけを使う（em dash 等を混ぜない）
        text = f"{did} [{_safe(role, 40)}] {_safe(path)}" + (f": {note}" if note else "")
        if status == "mismatch":
            problems.append(f"ref mismatch: {text}")
        elif status == "unverifiable":
            lines.append(f"  ref unverifiable: {text}")

    for did in sorted(records):
        rec = records[did]
        if rec.get("unparseable"):
            # frontmatter に対応しない構文がある。部分的に読めた分で「一致」と言わない（R2-2）
            record("unverifiable", did, "frontmatter", rec["rel"],
                   "frontmatter に対応しない構文があります（unparseable）")
            continue
        refs = []
        contract_ref = rec["fm"].get("effect_contract")
        if contract_ref is None:
            record("unverifiable", did, "effect-contract", rec["rel"], "proposal に effect_contract がありません")
        else:
            refs.append(("effect-contract", contract_ref))
        source_refs = rec["fm"].get("source_refs")
        if not isinstance(source_refs, list) or not source_refs:
            record("unverifiable", did, "source_refs", rec["rel"], "source_refs が非空の配列ではありません")
        else:
            for ref in source_refs:
                role = ref.get("role") if isinstance(ref, dict) else "?"
                refs.append((role if isinstance(role, str) else "?", ref))
        for role, ref in refs:
            if not isinstance(ref, dict):
                record("unverifiable", did, role, rec["rel"], "ref が object ではありません")
                continue
            path, expected = ref.get("path"), ref.get("sha256")
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                # R3-7: hash は**常に文字列**。数値で来たら桁を復元できない（先頭 0 が落ちる）ので
                # 「検証していない（unverifiable）」ではなく **形式不正の mismatch** として FAIL にする
                record("mismatch", did, role, path, "sha256 が数値です（64桁 hex の文字列で書いてください）")
                continue
            if not (isinstance(expected, str) and SHA256_RE.match(expected)):
                record("unverifiable", did, role, path, "sha256 の形式が不正です")
                continue
            kind, target, note = resolve_ref_path(root, path, ref_bases)
            if kind != "resolved":
                record("unverifiable", did, role, path, note)
                continue
            try:
                if not target.is_file():
                    record("mismatch", did, role, path, "実体がありません")
                    continue
                current = sha256_file(target)
            except OSError as e:
                record("unverifiable", did, role, path, f"読めません（{type(e).__name__}）")
                continue
            if current == expected:
                record("ok", did, role, path)
            else:
                record("mismatch", did, role, path,
                       f"{expected[:12]}… -> {current[:12]}…（再レビューが要ります）")
    # role は判定に使わず**報告に載せるだけ**（source ref の mismatch は「再レビューが要る」の意味）
    for role in sorted(by_role):
        c = by_role[role]
        lines.append(f"  refs role={role}: ok={c['ok']} mismatch={c['mismatch']} "
                     f"unverifiable={c['unverifiable']}")
    return problems, counts, lines


def cmd_validate(root: Path, *, strict_index: bool = True, refs: bool = False,
                 ref_bases: dict | None = None) -> int:
    """event 集合と派生 index の invariant を検査（単一 schema では表せない分・契約 §10）。

    `refs=True` のとき、proposal の `source_refs[]` / `effect_contract` を実体 hash と照合する（C）。
    照合できなかった ref は `unverifiable` として**別に数える**（FAIL にしない）。
    `bound_to` を持たない accepted は `legacy` として数える（FAIL にしない・R2-1）。
    """
    problems = []
    events, health_problems = store_health(root)    # validated events ＋ 全診断（定義は1箇所）
    problems += health_problems
    # 遷移の連鎖: chain を辿り、head 束縛（id と hash）と state 整合を検査
    by_id = {ev["event_id"]: ev for ev in events}
    all_dids = {(ev.get("subject") or {}).get("distill_id") for ev in events}
    for did in sorted(x for x in all_dids if x):
        try:
            chain = state_chain(events, did)
        except DistillError as e:
            problems.append(str(e))
            continue
        cur = "absent"
        for ev in chain:
            if ev.get("expected_previous_state") != cur:
                problems.append(f"{ev['event_id']}: expected_previous_state={ev.get('expected_previous_state')} "
                                f"but chain state={cur}")
            prev_id = ev.get("previous_event_id")
            if cur == "absent":
                if prev_id:
                    problems.append(f"{ev['event_id']}: absent からの遷移に previous_event_id があります")
            else:
                prev = by_id.get(prev_id)
                if prev is None:
                    problems.append(f"{ev['event_id']}: previous_event_id が見つかりません")
                elif prev.get("_sha256") != ev.get("previous_event_sha256"):
                    problems.append(f"{ev['event_id']}: previous_event_sha256 が実ファイルと不一致")
            cur = ev.get("new_state")
        # R4-2 / R4-3: 非 state event（registered / opportunity 等）も chain 上の位置で同じ検査を受ける。
        # 時刻の食い違いと、chain の page identity から外れた subject を拒む
        chain_page = (chain[-1].get("subject") or {}).get("page_path") if chain else None
        for ev in events:
            subj = ev.get("subject") or {}
            if subj.get("distill_id") != did or ev.get("event_type") in STATE_EVENTS:
                continue
            problem = event_time_problem(ev)
            if problem:
                problems.append(f"{did}: {problem}")
            page = subj.get("page_path")
            if chain_page and page and path_identity(page) != path_identity(chain_page):
                problems.append(f"{did}: subject identity changed（{ev['event_id']}: page_path "
                                f"{_safe(page)} は chain の {_safe(chain_page)} と異なります）")
    # opportunity ID の一意性（V2-R4）・terminal 最大1つ・先行 opportunity・subject 整合（V2-R3）
    opp_by_id = {}
    for ev in events:
        if ev.get("event_type") != "opportunity":
            continue
        oid = ev.get("opportunity_id")
        if oid in opp_by_id:
            problems.append(f"duplicate opportunity_id: {oid}（{opp_by_id[oid]['event_id']} と {ev['event_id']}）")
            continue
        opp_by_id[oid] = ev
    terminal = {}
    for ev in events:
        if ev.get("event_type") not in ("invoked",) + TERMINAL_EVENTS:
            continue
        oid = ev.get("opportunity_id")
        opp = opp_by_id.get(oid)
        if opp is None:
            problems.append(f"{ev['event_id']}: 先行 opportunity が存在しません（{oid}）")
            continue
        if subject_identity(opp.get("subject")) != subject_identity(ev.get("subject")):
            problems.append(f"{ev['event_id']}: 先行 opportunity {oid} と subject が一致しません "
                            f"（{subject_identity(opp.get('subject'))} != {subject_identity(ev.get('subject'))}）")
        if ev.get("event_type") in TERMINAL_EVENTS:
            if oid in terminal:
                problems.append(f"opportunity {oid}: terminal が複数（{terminal[oid]} と {ev['event_type']}）")
            else:
                terminal[oid] = ev["event_type"]
    # 派生 index は再生成と一致するか（C-03）
    idx = distill_dir(root) / "_index.md"
    if strict_index and idx.exists():
        if idx.read_text(encoding="utf-8").strip() != render_index(root, events).strip():
            problems.append("distill/_index.md が再生成結果と一致しません（reindex してください）")
    # page identity（R4）: 最後に page subject を束縛した event の hash と、現在の bytes を比較する。
    # drift は state を書き換えない（自動失効なし）が、再レビューまで fail-closed（契約 §7）
    for did, s in candidate_states(events).items():
        rel = s.get("page_path")
        if not rel:
            continue
        # authoritative な最新 page subject は **state chain の末尾**（人が review して束縛した時点）。
        # opportunity 等の非 state event が hash を持っていても、それは drift 解消の根拠にしない
        try:
            chain = state_chain(events, did)
        except DistillError:
            continue                      # chain の異常は上のブロックで報告済み
        head_ev = chain[-1] if chain else None
        try:
            p = resolved_page(root, rel)
        except DistillError as e:
            problems.append(f"{did}: {e}")
            continue
        if head_ev is None or not (head_ev.get("subject") or {}).get("page_sha256"):
            problems.append(f"{did}: page_sha256 を束縛した state event がありません")
            continue
        ev, subj = head_ev, head_ev["subject"]
        current = sha256_file(p)
        if current != subj["page_sha256"]:
            problems.append(f"{did}: page drift（{rel} は {ev['event_id']} 以降に変更されています: "
                            f"{subj['page_sha256'][:12]}… -> {current[:12]}…）。"
                            f"再 review して指名し直すか（`distill rereview`）、内容を戻してください")
    # accepted の hash 束縛（A）と proposal 文書の食い違い（D / E）
    states = candidate_states(events)
    records, prop_problems = scan_proposals(root)
    problems += prop_problems
    bound_problems, bound_legacy = check_bound_drift(root, events, states, records)
    problems += bound_problems
    problems += check_proposal_documents(root, records)
    # source_refs / effect_contract の実体照合（C・--refs のときだけ）
    ref_lines, ref_counts = [], None
    if refs:
        ref_problems, ref_counts, ref_lines = check_refs(root, records, ref_bases)
        problems += ref_problems
    for line in bound_legacy:
        print(f"  bound_to legacy: {line}")
    if bound_legacy:
        print(f"bound_to: legacy={len(bound_legacy)}"
              "（merge 3 より前の accepted。`distill rereview` で束縛し直すまで drift は検査しません）")
    if ref_counts is not None:
        for line in ref_lines:
            print(line)
        print(f"refs: ok={ref_counts['ok']} mismatch={ref_counts['mismatch']} "
              f"unverifiable={ref_counts['unverifiable']}"
              "（unverifiable は「検証していない」であり「一致」ではありません）")
    if problems:
        print("DISTILL-VALIDATE: FAIL")
        for p in problems:
            print(f"  {p}")
        return 2
    print(f"DISTILL-VALIDATE: OK (events={len(events)}, candidates={len(states)}, "
          f"legacy={len(bound_legacy)})")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _sha256_arg(value: str) -> str:
    if not SHA256_RE.match(value or ""):
        raise argparse.ArgumentTypeError("SHA-256 の 64桁 hex（小文字）で指定してください")
    return value


def _ref_base_arg(value: str) -> tuple[str, str]:
    """`--ref-base <id>=<abs_dir>`。id は1 segment、dir は実在する directory。"""
    base_id, sep, path = (value or "").partition("=")
    if not sep or not REF_BASE_ID_RE.match(base_id) or not path:
        raise argparse.ArgumentTypeError("形式は <base_id>=<dir>（例: eBay=I:/Workspace/eBay）")
    return base_id, path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="distill", description="Wiki→Skill蒸留トラック（契約 docs/distillation-contract.md）")
    ap.add_argument("--wiki-root", default=None)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("nominate", help="人の指名（frontmatter付与＋registered＋nominated）")
    p.add_argument("page")
    p.add_argument("--reason", default="")

    p = sub.add_parser("status", help="read-only の状態表示")
    p.add_argument("--distill-id", default=None)
    p.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    p.add_argument("--min-opportunities", type=int, default=DEFAULT_MIN_OPPORTUNITIES)

    p = sub.add_parser("decide", help="nominated -> held|rejected|accepted")
    p.add_argument("distill_id")
    p.add_argument("new_state", choices=["held", "rejected", "accepted"])
    p.add_argument("--reason", required=True)
    p.add_argument("--bundle-sha256", type=_sha256_arg, default=None,
                   help="accepted のとき candidate bundle hash も束縛する（任意）")

    p = sub.add_parser("rereview", help="人の再レビューを1イベントで記録（state は変えず page identity を束縛し直す）")
    p.add_argument("distill_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--bundle-sha256", type=_sha256_arg, default=None,
                   help="accepted の再レビューで candidate bundle hash も束縛し直す（任意）")
    p.add_argument("--drop-bundle", action="store_true",
                   help="直前の accepted の candidate_bundle 束縛を**明示的に**外す（既定は引き継ぐ）")

    p = sub.add_parser("note", help="opportunity / invoked / completed / blocked を記録")
    p.add_argument("--type", dest="event_type", required=True,
                   choices=list(OPPORTUNITY_EVENTS))
    p.add_argument("--distill-id", default=None)
    p.add_argument("--task-id", default=None)
    p.add_argument("--trigger-source", default="manual-procedure",
                   choices=["scheduled", "run-now", "explicit-invocation", "manual-procedure"])
    p.add_argument("--trigger-ref", default=None)
    p.add_argument("--opportunity-id", default=None)
    p.add_argument("--block-kind", default=None,
                   choices=["input_missing", "precondition_failed", "permission_pending",
                            "external_unavailable", "operator_cancelled"])
    p.add_argument("--source", default="agent-self-report",
                   choices=["host-task", "agent-self-report", "human"])
    p.add_argument("--strength", default="asserted", choices=["observed", "asserted", "unverifiable"])
    p.add_argument("--reason", default=None)
    p.add_argument("--task-metadata-json", default=None)
    p.add_argument("--unverifiable-reason", default=None)
    p.add_argument("--host", default=None,
                   help="dedupe key に使う host 識別子（既定: COMPUTERNAME/HOSTNAME）。source enum で代用しない")

    sub.add_parser("reindex", help="distill/_index.md を再生成")
    p = sub.add_parser("validate", help="event 集合と派生 index の invariant 検査")
    p.add_argument("--refs", action="store_true",
                   help="proposal の source_refs / effect_contract を実体 hash と照合する")
    p.add_argument("--ref-base", action="append", default=[], type=_ref_base_arg, metavar="ID=DIR",
                   help="Vault 外 ref の base（例: eBay=I:/Workspace/eBay）。複数指定可")

    a = ap.parse_args(argv)
    root = Path(a.wiki_root).resolve() if a.wiki_root else find_wiki_root()
    if root is None:
        print("ERROR: .wiki が見つかりません（--wiki-root で指定してください）", file=sys.stderr)
        return 2
    try:
        if a.command == "nominate":
            return cmd_nominate(root, a.page, a.reason)
        if a.command == "status":
            return cmd_status(root, a.distill_id, a.window_days, a.min_opportunities)
        if a.command == "decide":
            return cmd_decide(root, a.distill_id, a.new_state, a.reason, bundle_sha256=a.bundle_sha256)
        if a.command == "rereview":
            return cmd_rereview(root, a.distill_id, a.reason, bundle_sha256=a.bundle_sha256,
                                drop_bundle=a.drop_bundle)
        if a.command == "note":
            meta = json.loads(a.task_metadata_json) if a.task_metadata_json else None
            return cmd_note(root, a.event_type, distill_id=a.distill_id, task_id=a.task_id,
                            trigger_source=a.trigger_source, trigger_ref=a.trigger_ref,
                            opportunity_id=a.opportunity_id, block_kind=a.block_kind,
                            source=a.source, strength=a.strength, reason=a.reason,
                            task_metadata=meta, unverifiable_reason=a.unverifiable_reason, host=a.host)
        if a.command == "reindex":
            p = cmd_reindex(root)
            print(f"reindexed: {p}")
            return 0
        if a.command == "validate":
            return cmd_validate(root, refs=a.refs, ref_bases=dict(a.ref_base))
    except DistillError as e:
        print(f"DISTILL ERROR: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
