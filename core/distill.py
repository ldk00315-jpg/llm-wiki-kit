#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""wiki-distill — Wiki→Skill蒸留トラック（D）の CLI / library（merge 2）。

契約: `docs/distillation-contract.md`。schema: `schema/distill/*.schema.json`。

  python core/distill.py nominate <Page>   --reason "..."     # 人の指名（frontmatter付与＋event）
  python core/distill.py status [--distill-id d-xxxxxxxx]     # read-only（state と候補一覧）
  python core/distill.py decide <distill_id> <held|rejected|accepted> --reason "..."
  python core/distill.py note --type opportunity|invoked|completed|blocked ...
  python core/distill.py reindex                              # distill/_index.md の決定論的再生成
  python core/distill.py validate                             # event 集合と派生 index の invariant 検査

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
OPPORTUNITY_ID_RE = re.compile(r"^op-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
DISTILL_ID_RE = re.compile(r"^d-[0-9a-f]{8}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# portable_path（schema と同一の字句規則。segment 単位で . / .. / 空 / 制御文字を拒否）
_SEG = r"(?!\.\.?(?:/|$))[^/\\\x00-\x1f\x7f-\x9f]+"
PORTABLE_PATH_RE = re.compile(r"^(?![A-Za-z]:)(?!~)" + _SEG + r"(?:/" + _SEG + r")*$")

STATE_EVENTS = ("observed", "nominated", "decision")
OPPORTUNITY_EVENTS = ("opportunity", "invoked", "completed", "blocked")
CANDIDATE_STATES = ("absent", "observed", "nominated", "held", "rejected", "accepted")
# 遷移の正本（契約 §3 の表）: event_type -> {(source, from_state)} -> to_state
TRANSITIONS = {
    "observed": {"source": "system", "from": ("absent",), "to": ("observed",)},
    "nominated": {"source": "human", "from": ("absent", "observed", "held"), "to": ("nominated",)},
    "decision": {"source": "human", "from": ("nominated",), "to": ("held", "rejected", "accepted")},
}
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
    out.sort(key=lambda e: (e.get("occurred_at", ""), e.get("event_id", "")))
    return out, problems


def load_events(root: Path) -> list[dict]:
    """valid な event だけを返す（状態計算用）。診断が要る場面では scan_events を使う。"""
    return scan_events(root)[0]


def validate_event(ev: dict) -> list[str]:
    """schema（あれば jsonschema）＋遷移表による検証。jsonschema 不在でも最低限は検査する。"""
    problems = []
    payload = {k: v for k, v in ev.items() if not k.startswith("_")}   # _sha256 等の内部注釈は除く
    try:
        import jsonschema  # noqa: F401
        from jsonschema import Draft202012Validator as V
        schema = json.loads((SCHEMA_DIR / "distill-event.schema.json").read_text(encoding="utf-8"))
        problems += [f"schema: {e.message}" for e in V(schema).iter_errors(payload)]
    except ImportError:
        for k in ("event_id", "occurred_at", "event_type", "subject", "source", "strength", "actor"):
            if k not in payload:
                problems.append(f"missing field: {k}")
        if not EVENT_ID_RE.match(str(ev.get("event_id", ""))):
            problems.append("event_id format")
    et = ev.get("event_type")
    if et in TRANSITIONS:
        rule = TRANSITIONS[et]
        if ev.get("source") != rule["source"]:
            problems.append(f"{et}: source must be {rule['source']}")
        if ev.get("expected_previous_state") not in rule["from"]:
            problems.append(f"{et}: expected_previous_state must be one of {rule['from']}")
        if ev.get("new_state") not in rule["to"]:
            problems.append(f"{et}: new_state must be one of {rule['to']}")
        if (ev.get("subject") or {}).get("subject_type") != "page":
            problems.append(f"{et}: candidate state events require subject_type=page")
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
            ev = dict(ev, event_id=new_event_id())
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
# candidate state
# ---------------------------------------------------------------------------

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
        out[did] = {"state": chain[-1]["new_state"] if chain else "absent",
                    "head_event_id": chain[-1]["event_id"] if chain else None,
                    "page_path": page_path,
                    "updated": chain[-1].get("occurred_at") if chain else None}
    return out


def _base_event(event_type: str, subject: dict, source: str, actor: str, *, strength: str = "observed",
                reason: str | None = None) -> dict:
    ev = {"event_id": new_event_id(), "occurred_at": now_utc(), "event_type": event_type,
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
    lines.append("| distill_id | state | page | head event | updated |")
    lines.append("|---|---|---|---|---|")
    for did in sorted(states):
        s = states[did]
        lines.append(f"| {did} | {s['state']} | {s.get('page_path') or '-'} | "
                     f"{s.get('head_event_id') or '-'} | {s.get('updated') or '-'} |")
    lines.append("")
    return "\n".join(lines)


def reindex_locked(root: Path, events: list[dict] | None = None) -> Path:
    """**lock 保持済み**が前提の内部 helper（R3）。単体で呼ばない。"""
    p = distill_dir(root) / "_index.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, render_index(root, events))
    return p


def cmd_reindex(root: Path) -> Path:
    """CLI verb。lock を取ってから再生成する（並行する mutating verb との競合を避ける）。"""
    with VaultLock(root):
        return reindex_locked(root)


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


def assert_store_healthy(root: Path) -> list[dict]:
    """**lock 内で**呼ぶ。event store に破損があれば書き込みを拒否し、健全なら valid events を返す（V2-R1）。

    破損を放置したまま書くと、巻き戻った head の上に新しい event を積むことになり、
    immutable store へ回復困難な不整合を足してしまう。
    """
    events, problems = scan_events(root)
    if problems:
        raise DistillError("event store が壊れています（先に修復してください）:\n  " + "\n  ".join(problems))
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


def cmd_decide(root: Path, distill_id: str, new_state: str, reason: str, actor: str | None = None) -> int:
    actor = actor or _actor()
    if new_state not in ("held", "rejected", "accepted"):
        raise DistillError("decide の new_state は held|rejected|accepted")
    if not reason.strip():
        raise DistillError("--reason は必須です（decision は理由と共に記録する）")
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
        ev = _base_event("decision", subject, "human", actor, reason=reason)
        ev.update(expected_previous_state=state, new_state=new_state,
                  previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
        p = write_event(root, ev)
        reindex_locked(root)
    print(f"DECIDED {distill_id}: {state} -> {new_state}\n  event: distill/events/{p.name}")
    return 0


def cmd_note(root: Path, event_type: str, *, distill_id: str | None, task_id: str | None,
             trigger_source: str, trigger_ref: str | None, opportunity_id: str | None,
             block_kind: str | None, source: str, strength: str, reason: str | None,
             task_metadata: dict | None, unverifiable_reason: str | None, actor: str | None = None,
             host: str | None = None) -> int:
    """opportunity / invoked / completed / blocked を記録する（候補発見用・安全 gate ではない）。"""
    actor = actor or _actor()
    if event_type not in OPPORTUNITY_EVENTS:
        raise DistillError("note は opportunity|invoked|completed|blocked のみ")
    if distill_id:
        events = load_events(root)
        subj = None
        for ev in reversed(events):
            s = ev.get("subject") or {}
            if s.get("distill_id") == distill_id and s.get("subject_type") == "page":
                subj = dict(s)
                break
        if subj is None:
            raise DistillError(f"unknown distill_id: {distill_id}（先に nominate してください）")
        page = resolved_page(root, subj.get("page_path"))      # R2: resolver 経由
        subj["page_sha256"] = sha256_file(page)
        subject = subj
    elif task_id:
        subject = {"subject_type": "task", "task_id": task_id}
    else:
        raise DistillError("--distill-id か --task-id のどちらかが必要です")
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
    else:
        if not opportunity_id:
            raise DistillError(f"{event_type} には --opportunity-id が必要です（先行 opportunity を参照）")
        ev["opportunity_id"] = opportunity_id
        if event_type == "blocked":
            if not block_kind:
                raise DistillError("blocked には --block-kind が必要です")
            ev["block_kind"] = block_kind
    with VaultLock(root):
        # R5/V2-R1: 破損 store では書かない。先行 opportunity・subject 整合・terminal 重複・ID 一意性を書く前に検査
        events = assert_store_healthy(root)
        if event_type == "opportunity":
            if any(e.get("opportunity_id") == ev["opportunity_id"] and e.get("event_type") == "opportunity"
                   for e in events):
                raise DistillError(f"opportunity_id が既に存在します: {ev['opportunity_id']}（V2-R4: 全体で一意）")
        else:
            opp = next((e for e in events
                        if e.get("event_type") == "opportunity" and e.get("opportunity_id") == ev["opportunity_id"]), None)
            if opp is None:
                raise DistillError(f"先行 opportunity が見つかりません: {ev['opportunity_id']}")
            if subject_identity(opp.get("subject")) != subject_identity(subject):
                raise DistillError(f"先行 opportunity と subject が一致しません: "
                                   f"{subject_identity(opp.get('subject'))} != {subject_identity(subject)}")
            if event_type in TERMINAL_EVENTS:
                existing = [e["event_type"] for e in events
                            if e.get("event_type") in TERMINAL_EVENTS and e.get("opportunity_id") == ev["opportunity_id"]]
                if existing:
                    raise DistillError(f"opportunity {ev['opportunity_id']} は既に {existing[0]} です"
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
    events, load_problems = scan_events(root)
    if load_problems:
        print("WARNING: event store に問題があります（表示中の state は不完全です）:", file=sys.stderr)
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


def cmd_validate(root: Path, *, strict_index: bool = True) -> int:
    """event 集合と派生 index の invariant を検査（単一 schema では表せない分・契約 §10）。"""
    problems = []
    events, load_problems = scan_events(root)
    problems += [f"event store: {p}" for p in load_problems]
    seen_ids = set()
    valid = []          # V2-R2: schema を通った event だけを cross-event invariant の計算へ渡す
    for ev in events:
        eprobs = validate_event(ev)
        problems += [f"{ev.get('event_id')}: {p}" for p in eprobs]
        if ev["event_id"] in seen_ids:
            problems.append(f"duplicate event_id: {ev['event_id']}")
        seen_ids.add(ev["event_id"])
        if not eprobs:
            valid.append(ev)
    events = valid
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
                            f"再 review して指名し直すか、内容を戻してください")
    if problems:
        print("DISTILL-VALIDATE: FAIL")
        for p in problems:
            print(f"  {p}")
        return 2
    print(f"DISTILL-VALIDATE: OK (events={len(events)}, candidates={len(candidate_states(events))})")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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
    sub.add_parser("validate", help="event 集合と派生 index の invariant 検査")

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
            return cmd_decide(root, a.distill_id, a.new_state, a.reason)
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
            return cmd_validate(root)
    except DistillError as e:
        print(f"DISTILL ERROR: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
