# -*- coding: utf-8 -*-
"""merge 3 攻撃側プローブ（docs/merge3-redteam.md）。

**赤になる assert が成果**（穴の再現）。緑の probe は「崩せなかった観点」の記録。
core/ には触れない。store はすべて tmp_path。

実行: I:\\Workspace\\llm-wiki-kit\\.venv\\Scripts\\python.exe -m pytest tests/redteam_merge3_probes.py -q
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import distill as d  # noqa: E402

PAGE = """---
title: Monthly listing recon (CSV)
summary: 月次のCSV突合手順
type: concept
trust: trusted
distill_reviewed_by: tonsuke
distill_reviewed_at: 2026-09-05
procedure: true
created: 2026-09-05
updated: 2026-09-05
---

# 手順

1. Seller Hub から CSV を取得する
"""
SLUG = "monthly-listing-recon-csv"
OTHER = "other-slug"
CEILING = ("review+probe+sandbox: undeclared effects found there fail validation; "
           "no runtime deny claimed")
WIN = sys.platform == "win32"


def contract(slug):
    return {"contract_version": "0.1", "skill_slug": slug,
            "attestation": {"declared_by": "claude", "declared_at": "2026-09-05T09:00:00Z",
                            "guarantee_ceiling": CEILING},
            "effects": [{"id": "e01", "resource": "human", "target": "Seller Hub CSV",
                         "op": "human_action", "reversibility": "none", "idempotency": "idempotent",
                         "human_action": "required"}]}


class Vault:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / ".wiki"
        (self.root / "wiki" / "concepts").mkdir(parents=True)
        (self.root / "raw").mkdir()
        self.page_rel = "wiki/concepts/MonthlyListingReconCsv.md"
        self.page = self.root / self.page_rel
        self.page.write_text(PAGE, encoding="utf-8")

    # --- CLI wrappers -------------------------------------------------------
    def nominate(self, rel=None, reason="r"):
        rel = rel or self.page_rel
        assert d.cmd_nominate(self.root, rel, reason, actor="t") == 0
        return d.read_frontmatter(self.root / rel)["distill_id"]

    def events(self):
        return d.load_events(self.root)

    def head(self, did):
        return d.state_head(self.events(), did)[1]

    def validate(self, **kw):
        return d.cmd_validate(self.root, **kw)

    def snapshot(self):
        return sorted(p.name for p in d.events_dir(self.root).glob("*.json"))

    # --- proposal / contract fixtures ---------------------------------------
    def write_contract(self, slug=SLUG):
        pd = self.root / "distill" / slug
        pd.mkdir(parents=True, exist_ok=True)
        p = pd / "effect-contract.json"
        p.write_text(json.dumps(contract(slug), ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return p

    def write_proposal(self, did, slug=SLUG, source_refs=None, ec_ref=None, extra_lines=(), text=None):
        ec_path = self.write_contract(slug)
        p = self.root / "distill" / slug / "proposal.md"
        if text is not None:
            p.write_text(text, encoding="utf-8")
            return p, ec_path
        refs = source_refs if source_refs is not None else [
            {"path": self.page_rel, "sha256": d.sha256_file(self.page), "role": "wiki-page"}]
        ec = ec_ref if ec_ref is not None else {
            "path": f"distill/{slug}/effect-contract.json", "sha256": d.sha256_file(ec_path)}
        lines = ["---", 'proposal_version: "0.1"', f"skill_slug: {slug}", f"distill_id: {did}",
                 "kind: brownfield",
                 "source_refs: " + json.dumps(refs, ensure_ascii=False),
                 "effect_contract: " + json.dumps(ec, ensure_ascii=False)]
        lines += list(extra_lines) + ["---", "", "# proposal", ""]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p, ec_path

    def accept(self, did, **kw):
        self.write_proposal(did)
        return d.cmd_decide(self.root, did, "accepted", "ok", actor="t", **kw)

    def forged_decision(self, did, new_state="accepted", subject=None, bound_to=None, actor="forger",
                        later=False):
        """schema 的には正しい decision event を CLI を迂回して書く（write_event は validate を通す）。
        later=True は occurred_at を未来にして、同一秒の file 順に依存しない time-sort 末尾にする。"""
        head = self.head(did)
        subject = dict(head["subject"]) if subject is None else subject
        ev = d._base_event("decision", subject, "human", actor, reason="forged")
        ev.update(expected_previous_state="nominated", new_state=new_state,
                  previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
        if later:
            ev.update(occurred_at="2026-12-31T23:59:59Z", event_id="20261231T235959Z-" + ev["event_id"][-8:])
        if bound_to is not None:
            ev["bound_to"] = bound_to
        return d.write_event(self.root, ev)


@pytest.fixture
def v(tmp_path):
    return Vault(tmp_path)


# ===========================================================================
# 1. hash 束縛の偽装（bound_to）
# ===========================================================================

def test_A1_bound_to_effect_contract_may_point_at_any_file(v):
    """bound_to.effect_contract.path が effect-contract.json 以外（ここでは Wiki page）を指していても validate OK。
    check_bound_drift は path→hash の一致しか見ず、束縛先が『その candidate の effect contract か』を見ない。"""
    did = v.nominate()
    prop, _ = v.write_proposal(did)
    bound = {"proposal": {"path": f"distill/{SLUG}/proposal.md", "sha256": d.sha256_file(prop)},
             "effect_contract": {"path": v.page_rel, "sha256": d.sha256_file(v.page)}}
    v.forged_decision(did, bound_to=bound)
    d.cmd_reindex(v.root)
    assert v.validate() == 2, "effect contract の束縛先が Wiki page なのに OK（P1: gate が空の束縛で開く）"


def test_A2_bound_to_proposal_may_point_at_non_proposal_file_of_same_slug(v):
    """bound_to.proposal.path が distill/<slug>/notes.md（proposal ではない）でも validate OK。
    owners lookup に無い path は『別 candidate ではない』と扱われて素通りする。"""
    did = v.nominate()
    v.write_proposal(did)
    notes = v.root / "distill" / SLUG / "notes.md"
    notes.write_text("not a proposal\n", encoding="utf-8")
    ec = v.root / "distill" / SLUG / "effect-contract.json"
    bound = {"proposal": {"path": f"distill/{SLUG}/notes.md", "sha256": d.sha256_file(notes)},
             "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json", "sha256": d.sha256_file(ec)}}
    v.forged_decision(did, bound_to=bound)
    d.cmd_reindex(v.root)
    assert v.validate() == 2


def _other_candidate(v):
    """別 candidate（d-0ther000）の proposal / contract を distill/other-slug/ に置く。"""
    v.write_contract(OTHER)
    p = v.root / "distill" / OTHER / "proposal.md"
    p.write_text("---\nskill_slug: other-slug\ndistill_id: d-0ffe0000\nkind: brownfield\n---\n", encoding="utf-8")
    return p


def test_A3_control_exact_path_to_other_candidate_is_detected(v):
    """対照: 正しい綴りで別 candidate の proposal を束縛すると検出される（ここは緑のはず）。"""
    did = v.nominate()
    v.write_proposal(did)
    other = _other_candidate(v)
    ec = v.root / "distill" / SLUG / "effect-contract.json"
    bound = {"proposal": {"path": f"distill/{OTHER}/proposal.md", "sha256": d.sha256_file(other)},
             "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json", "sha256": d.sha256_file(ec)}}
    v.forged_decision(did, bound_to=bound)
    d.cmd_reindex(v.root)
    assert v.validate() == 2


@pytest.mark.skipif(not WIN, reason="Windows の path alias（大文字小文字）")
def test_A3_other_candidate_proposal_via_case_variant_path_bypasses_owner_check(v):
    """同じ file を『Distill/Other-Slug/PROPOSAL.MD』で束縛すると owners lookup を外れ、hash は実 file と一致 → OK。"""
    did = v.nominate()
    v.write_proposal(did)
    other = _other_candidate(v)
    ec = v.root / "distill" / SLUG / "effect-contract.json"
    alias = f"Distill/{OTHER.title()}/PROPOSAL.MD"
    bound = {"proposal": {"path": alias, "sha256": d.sha256_file(other)},
             "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json", "sha256": d.sha256_file(ec)}}
    v.forged_decision(did, bound_to=bound)
    d.cmd_reindex(v.root)
    assert v.validate() == 2, "case-variant path で別 candidate の proposal 束縛が『一致』扱い"


@pytest.mark.skipif(not WIN, reason="Windows の path alias（末尾ドット）")
def test_A4_trailing_dot_path_alias_passes_portable_path_and_resolves_to_real_file(v):
    """`proposal.md.`（末尾ドット）は PORTABLE_PATH_RE を通り、Windows では同じ file に解決する。"""
    did = v.nominate()
    v.write_proposal(did)
    other = _other_candidate(v)
    ec = v.root / "distill" / SLUG / "effect-contract.json"
    alias = f"distill/{OTHER}/proposal.md."
    assert d.PORTABLE_PATH_RE.match(alias)
    resolved = d.resolve_under_base(v.root, alias)
    assert resolved.is_file()
    bound = {"proposal": {"path": alias, "sha256": d.sha256_file(other)},
             "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json", "sha256": d.sha256_file(ec)}}
    v.forged_decision(did, bound_to=bound)
    d.cmd_reindex(v.root)
    assert v.validate() == 2


def test_A5_head_event_file_rewrite_is_undetectable(v):
    """head（chain 末尾）の event file は誰の previous_event_sha256 にも参照されない。
    proposal を書き換え、head の bound_to.proposal.sha256 を新 hash へ上書きすると validate OK。"""
    did = v.nominate()
    assert v.accept(did) == 0
    prop = v.root / "distill" / SLUG / "proposal.md"
    prop.write_text(prop.read_text(encoding="utf-8") + "\n悪意ある追記\n", encoding="utf-8")
    assert v.validate() == 2                      # 対照: 書き換えだけなら proposal drift で FAIL
    head = v.head(did)
    f = d.events_dir(v.root) / f"{head['event_id']}.json"
    raw = json.loads(f.read_text(encoding="utf-8"))
    raw["bound_to"]["proposal"]["sha256"] = d.sha256_file(prop)
    f.write_text(json.dumps(raw, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    d.cmd_reindex(v.root)
    assert v.validate() == 2, "head event の in-place 書き換えが検出されない（束縛の錨が無い）"


def test_A6_new_accepted_without_bound_to_is_legacy_not_fail(v):
    """merge 3 以降に CLI を迂回して書いた bound_to 無しの accepted は legacy 扱い（proposal が無くても OK）。"""
    did = v.nominate()
    v.forged_decision(did)                        # bound_to 無し・proposal.md も無し
    d.cmd_reindex(v.root)
    assert v.validate() == 2, "束縛の無い accepted が legacy として通る（新旧の区別が無い）"


def test_A7_sha256_case_and_whitespace_variants_are_rejected(v):
    """崩せない: 大文字 / 前後空白 / 63桁 の sha256 は builtin が拒む（store 不健全→全 verb 拒否）。"""
    did = v.nominate()
    prop, ec = v.write_proposal(did)
    good = d.sha256_file(prop)
    for bad in (good.upper(), " " + good, good + " ", good[:-1], good[:-1] + "g"):
        bound = {"proposal": {"path": f"distill/{SLUG}/proposal.md", "sha256": bad},
                 "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json", "sha256": d.sha256_file(ec)}}
        with pytest.raises(d.DistillError):
            v.forged_decision(did, bound_to=bound)


# ===========================================================================
# 2. 状態機械
# ===========================================================================

def test_B1_identity_migrates_to_another_page_through_cli(v):
    """別 page が同じ distill_id を frontmatter に持つと、held からその page を nominate できる。
    candidate identity（distill_id）が page_path をまたいで移る。契約 §2（Vault 内 unique）に反する。"""
    did = v.nominate()
    d.cmd_decide(v.root, did, "held", "later", actor="t")
    other_rel = "wiki/concepts/Other.md"
    (v.root / other_rel).write_text(v.page.read_text(encoding="utf-8").replace("# 手順", "# 別の手順"),
                                    encoding="utf-8")       # distill_id ごと複製
    with pytest.raises(d.DistillError):
        d.cmd_nominate(v.root, other_rel, "hijack", actor="t")
    head = v.head(did)
    assert head["subject"]["page_path"] == v.page_rel


def test_B2_forged_accepted_with_switched_page_path_validates_ok(v):
    """hand-written decision(accepted) で subject.page_path を別 page に差し替えても validate OK。
    chain は distill_id だけで繋ぎ、subject identity の連続性を検査しない。"""
    did = v.nominate()
    v.write_proposal(did)
    other_rel = "wiki/concepts/Other.md"
    (v.root / other_rel).write_text("---\ntitle: other\n---\nno review at all\n", encoding="utf-8")
    subject = {"subject_type": "page", "distill_id": did, "page_path": other_rel,
               "page_sha256": d.sha256_file(v.root / other_rel)}
    v.forged_decision(did, subject=subject, bound_to=d.compute_bound_to(v.root, did), later=True)
    d.cmd_reindex(v.root)
    # 注: 同一秒に書くと candidate_states の page_path が file 順で元 page になり drift で FAIL することもある
    #（=検出が乱数依存）。1秒でも後なら確実に OK になる
    assert v.validate() == 2, "review 無しの page へ accepted identity が移っても OK"


def test_B3_rereview_from_accepted_drops_candidate_bundle_binding(v):
    """accepted(bundle 束縛あり) → rereview（--bundle-sha256 無し）で candidate_bundle 束縛が黙って消える。"""
    did = v.nominate()
    assert v.accept(did, bundle_sha256="c" * 64) == 0
    assert "candidate_bundle" in v.head(did)["bound_to"]
    assert d.cmd_rereview(v.root, did, "re", actor="t") == 0
    assert "candidate_bundle" in v.head(did)["bound_to"], "再レビューで bundle 束縛が弱まる（警告も無い）"


def test_B4_rereviewed_from_held_absent_rejected_by_builtin(v):
    """崩せない: rereviewed を held / absent から出す hand-written event は builtin が拒む。"""
    did = v.nominate()
    d.cmd_decide(v.root, did, "held", "later", actor="t")
    head = v.head(did)
    for eps in ("held", "absent", "rejected"):
        ev = d._base_event("rereviewed", dict(head["subject"]), "human", "f", reason="r")
        ev.update(expected_previous_state=eps, new_state=eps if eps != "absent" else "nominated",
                  previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
        with pytest.raises(d.DistillError):
            d.write_event(v.root, ev)


def test_B5_branch_and_stale_previous_are_detected(v):
    """崩せない: previous_event_id を過去 event に向けた枝分かれは chain 検査で FAIL・全 verb 拒否。"""
    did = v.nominate()
    nominated = v.head(did)
    d.cmd_decide(v.root, did, "held", "later", actor="t")
    d.cmd_nominate(v.root, v.page_rel, "again", actor="t")
    ev = d._base_event("decision", dict(nominated["subject"]), "human", "f", reason="branch")
    ev.update(expected_previous_state="nominated", new_state="rejected",
              previous_event_id=nominated["event_id"], previous_event_sha256=nominated["_sha256"])
    d.write_event(v.root, ev)
    assert v.validate(strict_index=False) == 2
    with pytest.raises(d.DistillError):
        d.cmd_decide(v.root, did, "held", "x", actor="t")


def test_B6_rereviewed_twice_and_state_unchanged(v):
    """崩せない（仕様どおり）: rereviewed 2連続は許され、page_sha256 は末尾を指す。"""
    did = v.nominate()
    d.cmd_rereview(v.root, did, "a", actor="t")
    v.page.write_text(PAGE + "\n追記\n", encoding="utf-8")
    d.cmd_rereview(v.root, did, "b", actor="t")
    assert v.head(did)["subject"]["page_sha256"] == d.sha256_file(v.page)
    assert v.validate() == 0


# ===========================================================================
# 3. validate / proposal / 書き込み順序
# ===========================================================================

def test_C1_unparseable_proposal_is_still_accepted_by_decide(v):
    """block scalar を含む（YAML サブセット外＝unparseable）proposal でも decide accepted が通る。
    flat 抽出器の distill_id / skill_slug だけで identity が成立し、refs を永遠に照合できない proposal を束縛する。
    spec A『frontmatter が壊れている → 何も書かない』に反する。"""
    did = v.nominate()
    v.write_contract(SLUG)
    text = ("---\nproposal_version: \"0.1\"\n"
            f"skill_slug: {SLUG}\ndistill_id: {did}\nkind: brownfield\n"
            "note: |\n  multi\n  line\n"
            "---\n\n# p\n")
    v.write_proposal(did, text=text)
    before = v.snapshot()
    with pytest.raises(d.DistillError):
        d.cmd_decide(v.root, did, "accepted", "r", actor="t")
    assert v.snapshot() == before


def test_C2_apostrophe_in_plain_scalar_makes_whole_proposal_unparseable(v):
    """`note: it's fine` のような plain scalar（YAML では合法）で proposal 全体が unparseable になり、
    source_refs の mismatch（FAIL）が unverifiable（報告のみ）へ格下げされる。"""
    doc, probs = d.parse_frontmatter_yaml_subset("---\nk: it's fine\n---\n")
    assert doc == {"k": "it's fine"}, probs
    did = v.nominate()
    v.write_proposal(did, source_refs=[{"path": v.page_rel, "sha256": "0" * 64, "role": "wiki-page"}],
                     extra_lines=("note: don't trust this",))
    records, _ = d.scan_proposals(v.root)
    problems, counts, _ = d.check_refs(v.root, records, {})
    assert counts["mismatch"] == 1, (counts, problems)


def test_C3_compute_bound_to_reads_proposal_twice(v, monkeypatch):
    """docstring『1回だけ読んだ bytes から hash』に反し、frontmatter 検査（scan）と hash（read_bytes）は別読み。
    その間に差し替えると、skill_slug が別 candidate の proposal の hash を accepted が束縛する。"""
    did = v.nominate()
    prop, _ = v.write_proposal(did)
    validated = prop.read_bytes()
    swapped = validated.replace(f"skill_slug: {SLUG}".encode(), b"skill_slug: other-slug")
    orig = d.find_proposal

    def racy(root, distill_id):
        rec = orig(root, distill_id)
        prop.write_bytes(swapped)                   # 外部エディタは VaultLock を守らない
        return rec
    monkeypatch.setattr(d, "find_proposal", racy)
    bound = d.compute_bound_to(v.root, did)
    assert bound["proposal"]["sha256"] == d.sha256_bytes(validated), "検査していない bytes の hash を束縛"


def test_C4_all_digit_sha256_is_read_as_int_and_escapes_mismatch(v):
    """64桁すべて数字の sha256 は int になり、check_refs では『形式が不正 → unverifiable』。
    実体と食い違っていても mismatch（FAIL）にならない。先頭 0 は int 化で桁も失う。"""
    digits = "1234567890" * 6 + "1234"
    doc, _ = d.parse_frontmatter_yaml_subset(f"---\nsha256: {digits}\nzero: 0{digits[1:]}\n---\n")
    assert isinstance(doc["sha256"], str), type(doc["sha256"])
    assert str(doc["zero"]) == "0" + digits[1:]
    did = v.nominate()
    v.write_proposal(did, text=(
        f"---\nproposal_version: \"0.1\"\nskill_slug: {SLUG}\ndistill_id: {did}\nkind: brownfield\n"
        f"source_refs:\n  - path: {v.page_rel}\n    sha256: {digits}\n    role: wiki-page\n"
        f"effect_contract:\n  path: distill/{SLUG}/effect-contract.json\n  sha256: {digits}\n---\n"))
    records, _ = d.scan_proposals(v.root)
    _, counts, _ = d.check_refs(v.root, records, {})
    assert counts["mismatch"] == 2, counts


def test_C5_numeric_skill_slug_directory_is_dropped(v):
    """SLUG_RE は `12345678` を許すが parser が int にするため directory 名と一致せず proposal が消える。"""
    did = v.nominate()
    slug = "12345678"
    v.write_contract(slug)
    (v.root / "distill" / slug / "proposal.md").write_text(
        f"---\nskill_slug: {slug}\ndistill_id: {did}\nkind: brownfield\n---\n", encoding="utf-8")
    records, problems = d.scan_proposals(v.root)
    assert did in records, problems


def test_C6_ref_base_symlink_back_into_root_is_unverifiable(v, tmp_path):
    """崩せない: base 配下の symlink が root 内へ戻っても解決 path が base 外なので unverifiable。"""
    base = tmp_path / "base"
    base.mkdir()
    try:
        os.symlink(str(v.root / "wiki"), str(base / "link"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not permitted")
    kind, _, note = d.resolve_ref_path(v.root, "eBay/link/concepts/MonthlyListingReconCsv.md", {"eBay": base})
    assert kind == "unverifiable", note


def test_C7_dotdot_and_absolute_refs_are_unverifiable(v, tmp_path):
    """崩せない: `..` / 絶対 / base id だけ / 未登録 base は unverifiable（ok に混ざらない）。"""
    for p in ("eBay/../x", "/abs", "C:/x", "eBay", "nobase/x", "wiki/../raw/x"):
        kind, _, _ = d.resolve_ref_path(v.root, p, {"eBay": tmp_path})
        assert kind == "unverifiable", p


# ===========================================================================
# 4. proposal 文書検査（D）
# ===========================================================================

def _doc_problems(v, extra_lines):
    did = v.nominate()
    v.write_proposal(did, extra_lines=extra_lines)
    records, problems = d.scan_proposals(v.root)
    assert did in records, problems
    return d.check_proposal_documents(v.root, records)


def test_D1_unquoted_revisions_compare_as_floats_and_hide_mismatch(v):
    """`document_revision: 0.1` と `extensions.revision: 0.10` は別の版だが float 化で等しくなり検出されない。"""
    problems = _doc_problems(v, ("document_revision: 0.1", "extensions:", "  revision: 0.10"))
    assert problems, "0.1 と 0.10 の食い違いが報告されない"


def test_D2_quoted_vs_unquoted_same_revision_is_false_positive(v):
    """`document_revision: "0.10"` と `extensions.revision: 0.10`（同じ版）が食い違いとして報告される。"""
    problems = _doc_problems(v, ('document_revision: "0.10"', "extensions:", "  revision: 0.10"))
    assert not problems, problems


def test_D3_mapping_revision_is_always_reported_or_never_compared(v):
    """実物 fixture の形（extensions.revision が mapping）: top-level と一致していても str(dict) 比較で常に食い違い扱い。
    中の document_revision は比較されない。"""
    same = _doc_problems(v, ('document_revision: "0.10"', "extensions:", "  revision:",
                             '    document_revision: "0.10"'))
    assert not same, same


def test_D3b_mapping_revision_with_real_mismatch_message_is_garbage(v):
    problems = _doc_problems(v, ('document_revision: "0.11"', "extensions:", "  revision:",
                                 '    document_revision: "0.10"'))
    # 食い違いは報告されるが、比較相手は中の document_revision ではなく dict 全体（理由文に dict がそのまま載る）
    assert problems and '"document_revision"' not in problems[0], problems


# ===========================================================================
# 5. YAML サブセット parser
# ===========================================================================

def P(text):
    return d.parse_frontmatter_yaml_subset("---\n" + text + "\n---\n")


def test_E1_json_flow_duplicate_keys_silently_last_wins():
    """docstring『key 重複 → unparseable』に反し、1行 JSON 経路（json.loads）は重複 key を黙って後勝ちにする。"""
    doc, probs = P('x: {"a": 1, "a": 2}')
    assert doc is None, (doc, probs)


def test_E2_json_flow_accepts_nan_and_nested_despite_no_nesting_claim():
    """json.loads は NaN / Infinity / 入れ子を通す（YAML なら文字列 / 対応外）。"""
    doc, _ = P("x: [NaN, Infinity]")
    assert doc is None or all(isinstance(t, str) for t in doc["x"]), doc


def test_E3_split_key_regex_is_quadratic():
    """`a<空白×N>b: c` の1行で _yaml_split_key が O(N²)。40k 空白で数秒、1MB で validate が止まる。"""
    line = "a" + " " * 40000 + "b: c"
    t = time.time()
    d._yaml_split_key(line)
    assert time.time() - t < 1.0, "ReDoS: proposal 1行で validate を止められる"


def test_E4_quote_comment_and_indent_traps_are_closed():
    """崩せない: クォート内 `#`、`''` エスケープ、値+入れ子、継続行、タブ、block scalar、anchor、`---`。"""
    assert P('k: "a # b" # c')[0] == {"k": "a # b"}
    assert P("k: 'it''s # x' # y")[0] == {"k": "it's # x"}
    assert P("k: a#b")[0] == {"k": "a#b"}
    assert P("k: 2026-09-05")[0] == {"k": "2026-09-05"}
    assert P("k: yes")[0] == {"k": "yes"}
    for bad in ("k: v\n  nested: 1", "k: first\n  second", "- a\n  b", "\tk: v", "k: |\n  x", "k: >\n  x",
                "k: &a x", "k: *a", "k: !!str x", "k: [a, [b]]", "k: [a", 'k: "a',
                "a: 1\na: 2", "a:\n  b: 1\n c: 2", "a:\n  - 1\n  b: 2", "k: 'a' b", "k: - a", "? a: b",
                "k: {a: 1, a: 2}", "%YAML 1.2"):
        doc, probs = P(bad)
        assert doc is None and probs and probs[0].startswith(d.UNPARSEABLE_PREFIX), (bad, doc, probs)


def test_E5_flat_extractor_and_subset_parser_can_disagree_on_identity(v):
    """unparseable のとき identity は flat 抽出器に落ちる。flat は『最後の同名 key』を採り、
    subset parser は key 重複を unparseable にする——重複 distill_id の proposal は flat 経由で通る。"""
    did = v.nominate()
    v.write_contract(SLUG)
    v.write_proposal(did, text=(
        f"---\nskill_slug: {SLUG}\ndistill_id: d-00000000\ndistill_id: {did}\nkind: brownfield\n---\n"))
    records, problems = d.scan_proposals(v.root)
    assert did not in records, "key 重複（unparseable）の proposal が identity として採用される"


# ===========================================================================
# 6. stdout / 理由文
# ===========================================================================

def test_F1_safe_keeps_bidi_override_and_line_separator():
    """_safe は C0/C1 だけを潰す。U+202E（RLO）/ U+2028（LS）はそのまま stdout へ載る。"""
    s = d._safe("a\u202eb\u2028c")
    assert "\u202e" not in s and "\u2028" not in s, repr(s)


def test_F2_portable_path_allows_bidi_and_pipe_into_index(v):
    """page_path の RLO（U+202E）は portable_path を通り、index と stdout（NOMINATED 行）にそのまま載る（P3）。"""
    rel = "wiki/concepts/ab\u202e.md"
    (v.root / rel).write_text(PAGE, encoding="utf-8")
    did = v.nominate(rel)
    assert v.validate() == 0
    assert did
    assert "\u202e" not in (v.root / "distill" / "_index.md").read_text(encoding="utf-8"), "RLO \u304c index / stdout \u306b\u7d20\u901a\u308a"


# ===========================================================================
# 7. R3 再確認（docs/merge3-spec-r3.md の修正に対する攻撃側の再検査）
#    G = 「再確認で新たに開いた／残った」経路。緑のものは「崩せなかった」の記録
# ===========================================================================

KELVIN = "\u212a"      # KELVIN SIGN: str.lower() は 'k' に畳むが、NTFS は 'k' と別の名前として扱う


def _forge_state_event(v, did, event_type, new_state, subject, *, occurred_at=None, **extra):
    head = v.head(did)
    ev = d._base_event(event_type, subject, "human", "forger", reason="forged")
    ev.update(expected_previous_state=head["new_state"], new_state=new_state,
              previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
    if occurred_at:
        ev.update(occurred_at=occurred_at,
                  event_id=occurred_at.replace("-", "").replace(":", "") + "-" + ev["event_id"][-8:])
    ev.update(extra)
    return d.write_event(v.root, ev)


@pytest.mark.skipif(not WIN, reason="Windows: str.lower() と NTFS の大小文字畳み込みの差")
def test_G1_kelvin_sign_alias_passes_normalized_equality_but_resolves_to_decoy_file(v):
    """R3-1/R3-2 の等値検査は `normalize_portable`（= `.lower()`）同士。U+212A（KELVIN SIGN）は
    `.lower()` で 'k' になるので `distill/<KELVIN>it-slug/proposal.md` は `distill/kit-slug/proposal.md` と
    **等しい**と判定される。ところが NTFS は U+212A を 'k' に畳まないので、resolve 先は攻撃者が置いた
    別 directory の decoy file。hash は decoy と一致 → validate OK（P1: 束縛先が candidate の外）。"""
    slug = "kit-slug"
    did = v.nominate()
    v.write_proposal(did, slug=slug)
    decoy_dir = v.root / "distill" / (KELVIN + "it-slug")
    decoy_dir.mkdir()
    decoy = decoy_dir / "proposal.md"
    decoy.write_text("---\nskill_slug: evil\ndistill_id: d-0ffe0000\n---\nnot the reviewed proposal\n",
                     encoding="utf-8")
    assert not os.path.samefile(str(decoy), str(v.root / "distill" / slug / "proposal.md"))
    alias = f"distill/{KELVIN}it-slug/proposal.md"
    assert d.normalize_portable(alias) == d.normalize_portable(f"distill/{slug}/proposal.md")
    ec = v.root / "distill" / slug / "effect-contract.json"
    bound = {"proposal": {"path": alias, "sha256": d.sha256_file(decoy)},
             "effect_contract": {"path": f"distill/{slug}/effect-contract.json", "sha256": d.sha256_file(ec)}}
    v.forged_decision(did, bound_to=bound)
    d.cmd_reindex(v.root)
    assert v.validate() == 2, "KELVIN SIGN alias で decoy proposal の束縛が『自分の proposal』扱い"


@pytest.mark.skipif(not WIN, reason="Windows: str.lower() と NTFS の大小文字畳み込みの差")
def test_G2_kelvin_sign_page_path_hijacks_identity_through_cli(v):
    """B1 の変種: 正本 page `wiki/concepts/kit.md` の frontmatter を `wiki/concepts/<KELVIN>it.md`（NTFS 上は
    別 file）へ複製すると、`cmd_nominate` の一意性検査（normalize 等値）も `state_chain` の R3-3 検査も
    「同じ page」と見なし、held から別 page へ identity が移る（P1）。"""
    rel = "wiki/concepts/kit.md"
    (v.root / rel).write_text(PAGE, encoding="utf-8")
    did = v.nominate(rel)
    d.cmd_decide(v.root, did, "held", "later", actor="t")
    evil_rel = f"wiki/concepts/{KELVIN}it.md"
    (v.root / evil_rel).write_text((v.root / rel).read_text(encoding="utf-8").replace("# 手順", "# 乗っ取り"),
                                   encoding="utf-8")
    assert not os.path.samefile(str(v.root / rel), str(v.root / evil_rel))
    with pytest.raises(d.DistillError):
        d.cmd_nominate(v.root, evil_rel, "hijack", actor="t")
    assert v.head(did)["subject"]["page_path"] == rel


def test_G3_backdated_accepted_without_bound_to_is_legacy(v):
    """R3-9 の cutoff は攻撃者が書ける `occurred_at` で判定する。chain の順序（previous_event_id）は
    時刻の単調性を要求しないので、nominated(今日) の後ろに `occurred_at=2026-09-08` の accepted を繋ぐと
    legacy（報告のみ・validate OK）になる——proposal も effect contract も無いのに gate が開く（P1）。"""
    did = v.nominate()
    subject = dict(v.head(did)["subject"])
    _forge_state_event(v, did, "decision", "accepted", subject, occurred_at="2026-09-08T23:59:59Z")
    d.cmd_reindex(v.root)
    assert v.validate() == 2, "backdate した束縛無し accepted が legacy として通る"


def test_G4_page_drift_check_follows_page_path_of_last_non_state_event(v):
    """R3-3 は state chain の page_path 連続性だけを見る。`candidate_states` の page_path は
    **file 順で最後の（state でない）event** から採るので、drift した正本 page の代わりに
    「承認時点の bytes をコピーした別 page」を指す opportunity event を1つ書くと page drift が消える（P1）。"""
    did = v.nominate()
    assert v.accept(did) == 0
    original = v.page.read_bytes()
    v.page.write_bytes(original + "\n承認後の改変\n".encode("utf-8"))
    assert v.validate() == 2                       # 対照: page drift で FAIL
    other_rel = "wiki/concepts/ArchivedCopy.md"
    (v.root / other_rel).write_bytes(original)
    subject = {"subject_type": "page", "distill_id": did, "page_path": other_rel,
               "page_sha256": d.sha256_bytes(original)}
    ev = d._base_event("opportunity", subject, "host-task", "forger", reason="forged")
    ev.update(opportunity_id=d.new_opportunity_id(),
              trigger={"trigger_source": "scheduled", "trigger_ref": "x", "task_metadata_status": "unverifiable",
                       "unverifiable_reason": "forged"},
              occurred_at="2026-12-31T23:59:59Z", event_id="20261231T235959Z-" + ev["event_id"][-8:])
    d.write_event(v.root, ev)
    d.cmd_reindex(v.root)
    assert v.validate() == 2, "非 state event の page_path で page drift 検査が別 page へ逸れる"


def test_G5_same_second_switched_page_path_is_rejected_regardless_of_order(v):
    """崩せない: 同一秒（later 無し）で page_path を差し替えた forged accepted は chain 検査で FAIL。
    R3-3 の検査は previous_event_id の連鎖に沿うので file 名の乱数順に依存しない。"""
    did = v.nominate()
    v.write_proposal(did)
    other_rel = "wiki/concepts/Other.md"
    (v.root / other_rel).write_text(PAGE, encoding="utf-8")
    subject = {"subject_type": "page", "distill_id": did, "page_path": other_rel,
               "page_sha256": d.sha256_file(v.root / other_rel)}
    for _ in range(5):                            # 乱数順に依存しないことを複数回で確かめる
        p = v.forged_decision(did, subject=subject, bound_to=d.compute_bound_to(v.root, did))
        assert v.validate(strict_index=False) == 2
        with pytest.raises(d.DistillError):
            d.cmd_decide(v.root, did, "held", "x", actor="t")   # 壊れた chain の上には書けない
        p.unlink()


def test_G6_trailing_dot_and_segment_dot_aliases_of_own_proposal_are_rejected(v, capsys):
    """崩せない（(b) の確認）: 末尾ドット・segment 末尾ドットは字句を通るが等値で落ちる。"""
    did = v.nominate()
    prop, ec = v.write_proposal(did)
    for alias in (f"distill/{SLUG}/proposal.md.", f"distill/{SLUG}./proposal.md", f"distill./{SLUG}/proposal.md"):
        assert d.PORTABLE_PATH_RE.match(alias), alias
        bound = {"proposal": {"path": alias, "sha256": d.sha256_file(prop)},
                 "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json", "sha256": d.sha256_file(ec)}}
        p = v.forged_decision(did, bound_to=bound)
        d.cmd_reindex(v.root)
        capsys.readouterr()
        assert v.validate() == 2, alias
        assert "bound_to points outside its own candidate" in capsys.readouterr().out
        p.unlink()
        d.cmd_reindex(v.root)


def test_G7_A1_A2_A3_exact_reason_in_stdout(v, capsys):
    """崩せない: A1（page を effect contract に）・A2（notes.md）・A3（別 slug）は理由文つきで FAIL。"""
    did = v.nominate()
    prop, ec = v.write_proposal(did)
    other = _other_candidate(v)
    notes = v.root / "distill" / SLUG / "notes.md"
    notes.write_text("x\n", encoding="utf-8")
    cases = [
        ({"proposal": {"path": f"distill/{SLUG}/proposal.md", "sha256": d.sha256_file(prop)},
          "effect_contract": {"path": v.page_rel, "sha256": d.sha256_file(v.page)}}, "effect_contract"),
        ({"proposal": {"path": f"distill/{SLUG}/notes.md", "sha256": d.sha256_file(notes)},
          "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json", "sha256": d.sha256_file(ec)}}, "proposal"),
        ({"proposal": {"path": f"distill/{OTHER}/proposal.md", "sha256": d.sha256_file(other)},
          "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json", "sha256": d.sha256_file(ec)}}, "proposal"),
    ]
    for bound, key in cases:
        p = v.forged_decision(did, bound_to=bound)
        d.cmd_reindex(v.root)
        capsys.readouterr()
        assert v.validate() == 2
        out = capsys.readouterr().out
        assert f"bound_to points outside its own candidate（bound_to.{key} = " in out, out
        p.unlink()
        d.cmd_reindex(v.root)


def test_G8_A5_without_reindex_is_detected_by_index_head_sha(v):
    """R3-4 の設計どおり: head を書き換えて reindex を**忘れた**ら index の head sha 不一致で FAIL。
    （reindex した場合は A5 のとおり検出されない＝設計上の限界）"""
    did = v.nominate()
    assert v.accept(did) == 0
    prop = v.root / "distill" / SLUG / "proposal.md"
    prop.write_text(prop.read_text(encoding="utf-8") + "\n改変\n", encoding="utf-8")
    head = v.head(did)
    f = d.events_dir(v.root) / f"{head['event_id']}.json"
    raw = json.loads(f.read_text(encoding="utf-8"))
    raw["bound_to"]["proposal"]["sha256"] = d.sha256_file(prop)
    f.write_text(json.dumps(raw, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    assert v.validate() == 2
    idx = (v.root / "distill" / "_index.md").read_text(encoding="utf-8")
    assert head["_sha256"] in idx


def test_G9_R3_6_quote_first_values_still_protect_hash_and_apostrophe():
    """崩せない（R3-6 の回帰確認）: クォートで始まる値の中の `#` / `'` は保護され、plain の `'` は文字。"""
    assert P('k: "a # b" # c')[0] == {"k": "a # b"}
    assert P("k: \"it's # x\" # y")[0] == {"k": "it's # x"}
    assert P("k: 'it''s # x'")[0] == {"k": "it's # x"}
    assert P("k: it's fine # c")[0] == {"k": "it's fine"}
    assert P("k: it's#not-a-comment")[0] == {"k": "it's#not-a-comment"}
    assert P('k: say "hi" there # c')[0] == {"k": 'say "hi" there'}
    assert P("k: don't # c")[0] == {"k": "don't"}
    assert P("'it''s': v # c")[0] == {"it's": "v"}
    assert P('"k # x": it\'s # c')[0] == {"k # x": "it's"}
    assert P("s:\n  - it's\n  - \"a # b\"\n  - 'c''d' # e")[0] == {"s": ["it's", "a # b", "c'd"]}
    assert P("s:\n  - k: it's\n    q: \"# x\"")[0] == {"s": [{"k": "it's", "q": "# x"}]}
    assert P('r: [{"path": "it\'s # p", "sha256": "x"}]')[0] == {"r": [{"path": "it's # p", "sha256": "x"}]}
    assert P('e: "…（既定dry-run）。--apply は…"')[0] == {"e": "…（既定dry-run）。--apply は…"}
    assert P('k: "a" # b "c"')[0] == {"k": "a"}          # ` #` 以降は comment（YAML どおり）
    for bad in ("k: 'a'#b", "k: 'a' b", "k: 'tis", 'k: "a\\"'):
        doc, probs = P(bad)
        assert doc is None and probs and probs[0].startswith(d.UNPARSEABLE_PREFIX), (bad, doc, probs)


def test_G10_apostrophe_inside_plain_flow_item_is_still_unparseable():
    """残り（P3）: R3-6 は block scalar だけ。flow の中の plain `it's` は `_yaml_flow_end` / `_yaml_flow_split`
    が「クォート開始」と読み、行全体が unparseable になる（YAML では `[it's, b]` は合法）。"""
    doc, probs = P("k: [it's, b]")
    assert doc == {"k": ["it's", "b"]}, probs


def test_G11_cutoff_control_today_forged_accepted_without_bound_to_fails(v, capsys):
    """対照（(a) の確認）: 今日の時刻で書いた束縛無し accepted は cutoff 00:00Z 以降なので FAIL。"""
    did = v.nominate()
    v.forged_decision(did)
    d.cmd_reindex(v.root)
    capsys.readouterr()
    assert v.validate() == 2
    assert "merge 3 導入後" in capsys.readouterr().out


# ===========================================================================
# R4 最終再確認（G12〜G18）: G1〜G4 の閉じ方と、R4 が新しく開けた穴
# ===========================================================================

import datetime as _dt
import inspect


def _freeze_clock(monkeypatch, iso: str):
    """`_base_event` / `new_event_id` が読む時計を固定する（CLI 自身に過去・未来の時刻で書かせるため）。"""
    fixed = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")

    class Frozen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.replace(tzinfo=tz) if tz else fixed

    monkeypatch.setattr(d.datetime, "datetime", Frozen)


def _forge_state_event_at(v, did, event_type, new_state, subject, occurred_at, **extra):
    """R4-2 を通る（event_id prefix と同秒の）偽装 state event。"""
    return _forge_state_event(v, did, event_type, new_state, subject, occurred_at=occurred_at, **extra)


def test_G12_R4_1_identity_paths_do_not_use_normalize_portable(v, capsys):
    """崩せない（G1/G2 の閉じ方の確認）: identity 比較は `path_identity`（state_chain の R3-3・
    cmd_nominate の一意性・check_bound_drift の R3-1・validate の R4-3）で、`normalize_portable` は
    core に**呼び出し元が無い**（定義と docstring だけ）。KELVIN は `path_identity` では畳まれない。"""
    src = inspect.getsource(d)
    calls = [ln for ln in src.splitlines()
             if "normalize_portable(" in ln and not ln.lstrip().startswith(("def ", "#", "`", "*"))]
    assert calls == [], calls
    assert d.path_identity(f"distill/{KELVIN}it-slug/proposal.md") != d.path_identity("distill/kit-slug/proposal.md")
    if WIN:
        assert d.path_identity("Distill/KIT-slug/PROPOSAL.md") == d.path_identity("distill/kit-slug/proposal.md")
        # G1 の実 stdout: 束縛先が candidate の外として拒まれる理由文が出る
        slug = "kit-slug"
        did = v.nominate()
        v.write_proposal(did, slug=slug)
        decoy_dir = v.root / "distill" / (KELVIN + "it-slug")
        decoy_dir.mkdir()
        decoy = decoy_dir / "proposal.md"
        decoy.write_text("---\nskill_slug: evil\ndistill_id: d-0ffe0000\n---\nx\n", encoding="utf-8")
        ec = v.root / "distill" / slug / "effect-contract.json"
        v.forged_decision(did, bound_to={
            "proposal": {"path": f"distill/{KELVIN}it-slug/proposal.md", "sha256": d.sha256_file(decoy)},
            "effect_contract": {"path": f"distill/{slug}/effect-contract.json", "sha256": d.sha256_file(ec)}})
        d.cmd_reindex(v.root)
        capsys.readouterr()
        assert v.validate() == 2
        assert "bound_to points outside its own candidate" in capsys.readouterr().out


def test_G13_same_second_reordered_event_id_does_not_reopen_legacy(v, capsys):
    """崩せない: nominated と同じ秒・event_id を辞書順で**前**に置いた束縛無し accepted。chain は
    previous_event_id で辿るので順序は影響せず、occurred_at は今日（cutoff 以降）なので FAIL。"""
    did = v.nominate()
    head = v.head(did)
    subject = dict(head["subject"])
    ev = d._base_event("decision", subject, "human", "forger", reason="forged")
    ev.update(expected_previous_state="nominated", new_state="accepted",
              previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"],
              occurred_at=head["occurred_at"], event_id=head["event_id"][:17] + "00000000")
    assert ev["event_id"] < head["event_id"]
    d.write_event(v.root, ev)
    d.cmd_reindex(v.root)
    capsys.readouterr()
    assert v.validate() == 2
    assert "merge 3 導入後" in capsys.readouterr().out


def test_G14_future_dated_accepted_without_bound_to_fails(v):
    """崩せない: 未来時刻（prefix も一致）の束縛無し accepted は cutoff 以降なので FAIL。"""
    did = v.nominate()
    _forge_state_event_at(v, did, "decision", "accepted", dict(v.head(did)["subject"]), "2099-01-01T00:00:00Z")
    d.cmd_reindex(v.root)
    assert v.validate() == 2


def test_G15_backdated_accepted_on_pre_cutoff_chain_is_still_legacy(v, monkeypatch, capsys):
    """残り（P1・G3 の残余）: R4-2 は「直前の event 以上」しか要求しない。head が cutoff より前の
    chain（merge 3 導入前に CLI が書いた nominated / held）には、[head.occurred_at, cutoff) の窓に
    backdate した束縛無し accepted を**今**繋げる。単調性も prefix も通り、legacy として validate OK。
    proposal も effect contract も無い。実 Vault に cutoff 前の非 terminal candidate があれば同じ。"""
    _freeze_clock(monkeypatch, "2026-09-05T10:00:00Z")      # 導入前に CLI 自身が書いた chain
    did = v.nominate()
    d.cmd_decide(v.root, did, "held", "later", actor="t")
    d.cmd_nominate(v.root, v.page_rel, "again", actor="t")
    monkeypatch.undo()
    assert v.head(did)["occurred_at"] < d.MERGE3_CUTOFF
    subject = dict(v.head(did)["subject"])
    _forge_state_event_at(v, did, "decision", "accepted", subject, "2026-09-08T23:59:59Z")
    d.cmd_reindex(v.root)
    capsys.readouterr()
    rc = v.validate()
    out = capsys.readouterr().out
    assert rc == 2, f"cutoff 前の chain に backdate した束縛無し accepted が legacy として通る:\n{out}"


def test_G16_R4_3_fallback_for_chainless_candidate_cannot_shift_identity(v, capsys):
    """崩せない（判断3 の確認）: chain が無い candidate（opportunity だけ）の page_path fallback は
    drift 検査を通す根拠にならない——「page_sha256 を束縛した state event がありません」で FAIL。
    その did で別 page を nominate すれば一意性検査が拒み、偽装 root を繋げば R4-3 で FAIL。"""
    did = "d-0ffe0001"
    other_rel = "wiki/concepts/Other.md"
    (v.root / other_rel).write_text(PAGE, encoding="utf-8")
    subject = {"subject_type": "page", "distill_id": did, "page_path": other_rel,
               "page_sha256": d.sha256_file(v.root / other_rel)}
    ev = d._base_event("opportunity", subject, "host-task", "forger", reason="forged")
    ev.update(opportunity_id=d.new_opportunity_id(),
              trigger={"trigger_source": "scheduled", "trigger_ref": "x", "task_metadata_status": "unverifiable",
                       "unverifiable_reason": "forged"})
    d.write_event(v.root, ev)
    d.cmd_reindex(v.root)
    capsys.readouterr()
    assert v.validate() == 2
    assert "page_sha256 を束縛した state event がありません" in capsys.readouterr().out
    # 同じ did を frontmatter に持つ別 page を nominate → 一意性検査で拒否
    v.page.write_text(PAGE.replace("procedure: true", f"procedure: true\ndistill_id: {did}"), encoding="utf-8")
    with pytest.raises(d.DistillError, match="既に"):
        d.cmd_nominate(v.root, v.page_rel, "hijack", actor="t")
    # 偽装 root（別 page）を繋ぐ → 非 state event の subject が chain と食い違い FAIL
    root_subject = {"subject_type": "page", "distill_id": did, "page_path": v.page_rel,
                    "page_sha256": d.sha256_file(v.page)}
    ev2 = d._base_event("nominated", root_subject, "human", "forger", reason="forged")
    ev2.update(expected_previous_state="absent", new_state="nominated")
    d.write_event(v.root, ev2)
    d.cmd_reindex(v.root)
    capsys.readouterr()
    assert v.validate() == 2
    assert "subject identity changed" in capsys.readouterr().out


def test_G17_write_event_collision_retry_breaks_event_id_time_prefix(v, monkeypatch):
    """残り（P3・R4 随伴）: `_base_event` は 1つの時刻から id と occurred_at を作るが、`write_event` の
    衝突 retry は `new_event_id()`（**今の**時刻）で id だけ引き直す。秒境界をまたぐと prefix が
    occurred_at と食い違い、CLI が書いた event が R4-2 で FAIL する（8hex 衝突が前提・実害は稀）。"""
    did = v.nominate()
    head = v.head(did)
    ev = d._base_event("decision", dict(head["subject"]), "human", "t", reason="x")
    ev.update(expected_previous_state="nominated", new_state="held",
              previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
    (d.events_dir(v.root) / f"{ev['event_id']}.json").write_text("{}", encoding="utf-8")   # 衝突させる
    nxt = (_dt.datetime.strptime(ev["occurred_at"], "%Y-%m-%dT%H:%M:%SZ") + _dt.timedelta(seconds=1))
    monkeypatch.setattr(d, "new_event_id", lambda: nxt.strftime("%Y%m%dT%H%M%SZ") + "-0badbeef")
    p = d.write_event(v.root, ev)
    written = json.loads(p.read_text(encoding="utf-8"))
    assert d.event_time_problem(written) is None, d.event_time_problem(written)


def test_G18_clock_skew_bricks_chain_through_legit_cli(v, monkeypatch, capsys):
    """残り（P2・運用）: 時計が進んだ host（or 別 host との数秒のずれ）で CLI が 1 event 書くと、
    その後の正しい時刻の CLI 書き込みは「occurred_at goes backwards」で chain を恒久的に FAIL にする。
    CLI は書く前に head の occurred_at と比較しない（書いてから validate が落ちる）。append-only なので
    CLI で修復できない。"""
    _freeze_clock(monkeypatch, "2026-09-09T12:00:00Z")
    did = v.nominate()
    monkeypatch.undo()
    assert v.validate() == 0
    _freeze_clock(monkeypatch, "2026-09-09T11:59:57Z")      # 3秒遅れの host
    assert d.cmd_decide(v.root, did, "held", "x", actor="t") == 0     # 書けてしまう
    monkeypatch.undo()
    capsys.readouterr()
    rc = v.validate()
    out = capsys.readouterr().out
    assert rc == 0, f"3秒の時計差で chain が恒久 FAIL:\n{out}"


# ===========================================================================
# R5 再確認（G19〜G25）: G15 / G17 / G18 の直し方が新しく開けた穴
# ===========================================================================

# 2026-09-09 follow-up で固定リストは空になった（共有 Vault の legacy は rereview で束縛し直した）。
# 監査時の値を既定にして import できるようにする。legacy 系の probe は「legacy が存在した当時」の記録
LEGACY_ID = next(iter(d.LEGACY_ACCEPTED_EVENT_IDS), "20260908T001351Z-c8e02b64")
LEGACY_AT = (f"{LEGACY_ID[0:4]}-{LEGACY_ID[4:6]}-{LEGACY_ID[6:8]}T"
             f"{LEGACY_ID[9:11]}:{LEGACY_ID[11:13]}:{LEGACY_ID[13:15]}Z")


def _pre_cutoff_chain(v, monkeypatch):
    """merge 3 導入前に CLI 自身が書いた nominated chain（G15 と同じ前提）。"""
    _freeze_clock(monkeypatch, "2026-09-05T10:00:00Z")
    did = v.nominate()
    monkeypatch.undo()
    return did


def _legacy_named_event(v, did, new_state="accepted", actor="forger"):
    head = v.head(did)
    ev = d._base_event("decision", dict(head["subject"]), "human", actor, reason="legacy-named")
    ev.update(expected_previous_state="nominated", new_state=new_state,
              previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"],
              occurred_at=LEGACY_AT, event_id=LEGACY_ID)
    return d.write_event(v.root, ev)


def test_G19_legacy_id_is_matched_by_name_only_and_can_be_planted_in_any_other_vault(v, monkeypatch, capsys):
    """残り（G15 の残余）: 固定リストは **event_id の文字列**だけで照合し、distill_id・subject・内容 hash を
    見ない。共有 Vault ではその id の file が既に在るので exclusive create が拒むが、**それ以外の Vault**
    （新しい Vault・複製・別 host）では id が空いているので、任意の candidate に proposal 無しの accepted を
    `20260908T001351Z-c8e02b64` という名前で書けば legacy（rc 0）として通る。"""
    did = _pre_cutoff_chain(v, monkeypatch)
    _legacy_named_event(v, did)
    d.cmd_reindex(v.root)
    capsys.readouterr()
    rc = v.validate()
    out = capsys.readouterr().out
    assert rc == 2, f"legacy id を名乗るだけで、別 Vault の任意 candidate が束縛無し accepted で通る:\n{out}"


def test_G20_legacy_allowance_survives_rereview_if_successor_is_deleted(v, monkeypatch, capsys):
    """残り（G15 × rereview）: rereview で head が bound_to 付きになれば固定リストは**もう参照されない**
    （head が legacy id でなくなる）。ただし後続 event の file を消して reindex すれば head は legacy id に
    戻り、再び legacy（rc 0）。固定リストは「rereview 後に外す」まで生き続ける。A5 と同じ「store への
    直接書き込み（ここでは削除）」前提なので P2 として記録。"""
    did = _pre_cutoff_chain(v, monkeypatch)
    _legacy_named_event(v, did, actor="t")
    d.cmd_reindex(v.root)
    capsys.readouterr()
    assert v.validate() == 0
    assert "legacy=1" in capsys.readouterr().out
    # 正規経路: rereview で束縛し直す → legacy は消える
    v.write_proposal(did)
    assert d.cmd_rereview(v.root, did, "rebind", actor="t") == 0
    capsys.readouterr()
    assert v.validate() == 0
    assert "legacy=0" in capsys.readouterr().out
    assert v.head(did)["event_id"] != LEGACY_ID
    # 攻撃: 後続 event を消して reindex → legacy id が head に戻る
    (d.events_dir(v.root) / f"{v.head(did)['event_id']}.json").unlink()
    d.cmd_reindex(v.root)
    capsys.readouterr()
    rc = v.validate()
    out = capsys.readouterr().out
    assert rc == 2, f"rereview 後に後続を消すと legacy が復活する（固定リストが retire されない）:\n{out}"


def test_G21_forged_future_head_within_skew_makes_cli_stamp_every_event_with_head_time(v, capsys):
    """G18 の clamp: head を +299 秒の未来に偽装すると、以後の CLI 書き込みは（実時刻が head を越えるまで）
    **全部 head と同じ秒**に揃えられる。chain の順序は previous_event_id で決まり、時刻は順序の材料に
    ならない（同秒・token 逆順でも head は変わらない）ので、**順序の偽装には使えない**。
    ただし記録される occurred_at は実際より最大 300 秒**進む**（後ろにはずれない）。"""
    did = v.nominate()
    head = v.head(did)
    future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=299)).replace(tzinfo=None)
    at = future.strftime("%Y-%m-%dT%H:%M:%SZ")
    _forge_state_event_at(v, did, "decision", "held", dict(head["subject"]), at)
    d.cmd_reindex(v.root)
    assert d.cmd_nominate(v.root, v.page_rel, "again", actor="t") == 0
    assert d.cmd_decide(v.root, did, "held", "again", actor="t") == 0
    chain = d.state_chain(v.events(), did)
    assert [e["new_state"] for e in chain] == ["nominated", "held", "nominated", "held"]
    assert {e["occurred_at"] for e in chain[1:]} == {at}          # 3件とも head の秒
    assert all(d.event_time_problem(e) is None for e in chain)
    capsys.readouterr()
    assert v.validate() == 0
    # 同秒に並んだ event を token 逆順で渡しても chain の head は変わらない
    reordered = sorted(v.events(), key=lambda e: e["event_id"], reverse=True)
    assert d.state_head(reordered, did)[1]["event_id"] == chain[-1]["event_id"]


def test_G22_forged_future_head_beyond_skew_refuses_cli_writes_until_wall_clock_catches_up(v):
    """G18 の拒否側: head を +301 秒以上の未来に偽装すると、CLI は「clock regression」で**書かない**
    （chain は壊れない）。実時刻が head を越えれば再び書ける。既知の R4-2 と同じ「書き込み権のある
    攻撃者は chain を止められる」の範囲で、何も書かないので append-only store は無傷。"""
    did = v.nominate()
    head = v.head(did)
    future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=306)).replace(tzinfo=None)
    _forge_state_event_at(v, did, "decision", "held", dict(head["subject"]),
                          future.strftime("%Y-%m-%dT%H:%M:%SZ"))
    d.cmd_reindex(v.root)
    before = v.snapshot()
    with pytest.raises(d.DistillError, match="clock regression"):
        d.cmd_nominate(v.root, v.page_rel, "again", actor="t")
    assert v.snapshot() == before                                    # 何も書かれていない
    assert v.validate() == 0


def test_G23_clamp_boundary_and_bad_head_time(v):
    """_align_clock の境界: ちょうど 300 秒は揃える・301 秒は拒む・head が同秒以降なら触らない・
    head の occurred_at が壊れていれば拒む（黙って通さない）。"""
    head = {"event_id": "20260909T120000Z-00000000", "occurred_at": "2026-09-09T12:00:00Z"}

    def ev_at(iso):
        return {"event_id": iso.replace("-", "").replace(":", "") + "-deadbeef", "occurred_at": iso}

    same = d._align_clock(head, ev_at("2026-09-09T12:00:00Z"))
    assert same["event_id"].endswith("-deadbeef") and same["occurred_at"] == "2026-09-09T12:00:00Z"
    later = d._align_clock(head, ev_at("2026-09-09T12:00:01Z"))
    assert later["occurred_at"] == "2026-09-09T12:00:01Z"
    edge = d._align_clock(head, ev_at("2026-09-09T11:55:00Z"))          # 300s
    assert edge["occurred_at"] == "2026-09-09T12:00:00Z"
    assert edge["event_id"] == "20260909T120000Z-deadbeef"
    assert d.event_time_problem(edge) is None
    with pytest.raises(d.DistillError, match="clock regression"):
        d._align_clock(head, ev_at("2026-09-09T11:54:59Z"))            # 301s
    # 壊れた head 時刻（字句順で新 event より後ろに並ぶもの）は strptime で拒む。字句順で前に並ぶ壊れ方
    # （例: "2026-09-09 12:00:00"）は「head より前ではない」として素通りするが、それは store_health が
    # 先に schema で落とす形式なので CLI からは到達しない
    with pytest.raises(d.DistillError, match="clock regression"):
        d._align_clock({"event_id": "x", "occurred_at": "2026-09-09T12:00:00+00:00"},
                       ev_at("2026-09-09T11:59:00Z"))
    assert d._align_clock(None, ev_at("2026-09-09T11:00:00Z"))["occurred_at"] == "2026-09-09T11:00:00Z"


def test_G24_collision_retry_keeps_clamped_prefix_and_exhaustion_writes_nothing(v, monkeypatch):
    """G17 × G18: clamp 後の event が衝突しても retry は head の秒 prefix を保ち、token だけ変わる。
    retry を使い切ったら DistillError で**何も書かない**（prefix 不一致の event を落とさない）。"""
    _freeze_clock(monkeypatch, "2026-09-09T12:00:00Z")
    did = v.nominate()
    monkeypatch.undo()
    head = v.head(did)
    _freeze_clock(monkeypatch, "2026-09-09T11:59:00Z")                # 60秒遅れの host
    ev = d._base_event("decision", dict(head["subject"]), "human", "t", reason="x")
    monkeypatch.undo()
    ev = d._align_clock(head, ev)
    ev.update(expected_previous_state="nominated", new_state="held",
              previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
    assert ev["event_id"][:16] == head["event_id"][:16]
    (d.events_dir(v.root) / f"{ev['event_id']}.json").write_text("{}", encoding="utf-8")   # 衝突
    p = d.write_event(v.root, ev)
    written = json.loads(p.read_text(encoding="utf-8"))
    assert written["event_id"][:16] == head["event_id"][:16]
    assert written["event_id"] != ev["event_id"]
    assert d.event_time_problem(written) is None
    # 使い切り: 常に衝突させる
    before = v.snapshot()

    def always_exists(*a, **k):
        raise FileExistsError("x")

    monkeypatch.setattr(d.os, "open", always_exists)
    with pytest.raises(d.DistillError, match="collision"):
        d.write_event(v.root, dict(ev, event_id=ev["event_id"][:16] + "-ffffffff"))
    monkeypatch.undo()
    assert v.snapshot() == before


def test_G25_legacy_id_with_non_accepted_state_is_not_legacy(v, monkeypatch, capsys):
    """記録（緑）: legacy id が head でも new_state が accepted 以外なら legacy 扱いにならない
    （リストは check_bound_drift の accepted 分岐からしか参照されない）。"""
    did = _pre_cutoff_chain(v, monkeypatch)
    _legacy_named_event(v, did, new_state="held", actor="t")
    d.cmd_reindex(v.root)
    capsys.readouterr()
    assert v.validate() == 0
    assert "legacy=0" in capsys.readouterr().out
