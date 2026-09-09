# -*- coding: utf-8 -*-
"""wiki-distill（merge 2）: state machine・exclusive create・lock・atomic・並行・validator の契約テスト。
外部依存なし（jsonschema があれば event schema でも検証される）。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import distill as d  # noqa: E402
import llmwiki as w  # noqa: E402

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
2. 突合する
"""

SLUG = "monthly-listing-recon-csv"
CEILING = ("review+probe+sandbox: undeclared effects found there fail validation; "
           "no runtime deny claimed")


def effect_contract(slug=SLUG, **extra):
    """`effect-contract.schema.json` に適合する最小の contract（merge 3 A / E の fixture）。"""
    c = {"contract_version": "0.1", "skill_slug": slug,
         "attestation": {"declared_by": "claude", "declared_at": "2026-09-05T09:00:00Z",
                         "guarantee_ceiling": CEILING},
         "effects": [{"id": "e01", "resource": "human", "target": "Seller Hub CSV",
                      "op": "human_action", "reversibility": "none", "idempotency": "idempotent",
                      "human_action": "required"}]}
    c.update(extra)
    return c


class DistillCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="distill-test-")
        self.root = Path(self.tmp.name) / ".wiki"
        (self.root / "wiki" / "concepts").mkdir(parents=True)
        (self.root / "raw").mkdir(parents=True)
        self.page_rel = "wiki/concepts/MonthlyListingReconCsv.md"
        self.page = self.root / self.page_rel
        self.page.write_text(PAGE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _nominate(self, reason="pilot 第2候補"):
        rc = d.cmd_nominate(self.root, self.page_rel, reason, actor="tonsuke")
        self.assertEqual(rc, 0)
        return d.read_frontmatter(self.page)["distill_id"]

    def _events(self):
        return d.load_events(self.root)

    def _last(self, event_type):
        """同一秒の event は file 順が不定なので、type で絞って取る（[-1] に依存しない）。"""
        evs = [e for e in self._events() if e["event_type"] == event_type]
        self.assertTrue(evs, f"no {event_type} event")
        return evs[-1]

    def _write_contract(self, slug=SLUG, contract=None):
        pd = self.root / "distill" / slug
        pd.mkdir(parents=True, exist_ok=True)
        p = pd / "effect-contract.json"
        p.write_text(json.dumps(effect_contract(slug) if contract is None else contract,
                                ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return p

    def _write_proposal(self, did, slug=SLUG, source_refs=None, effect_contract_ref=None,
                        extra_lines=(), contract=None, skill_slug=None):
        """merge 3 A: `decide accepted` は `distill/<slug>/proposal.md` と `effect-contract.json` を要求する。

        proposal frontmatter の入れ子 field は **1行の JSON flow 形式**（YAML flow style と互換）で書く。
        """
        ec_path = self._write_contract(slug, contract)
        refs = source_refs if source_refs is not None else [
            {"path": self.page_rel, "sha256": d.sha256_file(self.page), "role": "wiki-page"}]
        ec_ref = effect_contract_ref if effect_contract_ref is not None else {
            "path": f"distill/{slug}/effect-contract.json", "sha256": d.sha256_file(ec_path)}
        lines = ["---", "proposal_version: 0.1",
                 f"skill_slug: {slug if skill_slug is None else skill_slug}",
                 f"distill_id: {did}", "kind: brownfield",
                 "source_refs: " + json.dumps(refs, ensure_ascii=False),
                 "effect_contract: " + json.dumps(ec_ref, ensure_ascii=False)]
        lines += list(extra_lines) + ["---", "", "# proposal", ""]
        p = self.root / "distill" / slug / "proposal.md"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p, ec_path

    def _accept(self, did, reason="sandbox 証拠を確認", actor="tonsuke", **kw):
        """merge 3 A 以降、accepted は proposal / effect contract の hash 束縛を伴う。"""
        self._write_proposal(did)
        return d.cmd_decide(self.root, did, "accepted", reason, actor=actor, **kw)


class TestNominate(DistillCase):
    def test_nominate_registers_id_and_writes_two_events(self):
        did = self._nominate()
        self.assertRegex(did, r"^d-[0-9a-f]{8}$")
        evs = self._events()
        self.assertEqual(sorted(e["event_type"] for e in evs), ["nominated", "registered"])
        self.assertEqual(self._last("registered")["subject"]["distill_id"], did)
        nom = self._last("nominated")
        self.assertEqual(nom["expected_previous_state"], "absent")
        self.assertEqual(nom["new_state"], "nominated")
        self.assertNotIn("previous_event_id", nom)   # absent からは previous を持たない
        self.assertEqual(nom["subject"]["page_sha256"], d.sha256_file(self.page))
        self.assertEqual(d.state_head(evs, did)[0], "nominated")

    def test_frontmatter_gets_distill_id_and_defaults(self):
        did = self._nominate()
        fm = d.read_frontmatter(self.page)
        self.assertEqual(fm["distill_id"], did)
        self.assertEqual(fm["procedure"], "true")
        self.assertEqual(fm["distilled_to"], "[]")
        self.assertIn("# 手順", self.page.read_text(encoding="utf-8"))   # 本文は不変

    def test_refuses_unreviewed_page(self):
        p = self.root / "wiki" / "concepts" / "Unreviewed.md"
        p.write_text(PAGE.replace("trust: trusted\n", "").replace("distill_reviewed_by: tonsuke\n", ""),
                     encoding="utf-8")
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_nominate(self.root, "wiki/concepts/Unreviewed.md", "x", actor="t")
        self.assertIn("trust", str(cm.exception))
        self.assertEqual(self._events(), [])

    def test_refuses_trust_not_trusted(self):
        p = self.root / "wiki" / "concepts" / "Untrusted.md"
        p.write_text(PAGE.replace("trust: trusted", "trust: unverified"), encoding="utf-8")
        with self.assertRaises(d.DistillError):
            d.cmd_nominate(self.root, "wiki/concepts/Untrusted.md", "x", actor="t")
        self.assertEqual(self._events(), [])

    def test_refuses_path_escape(self):
        for bad in ("../outside.md", "/abs.md", "C:/x.md", "wiki/../../x.md"):
            with self.subTest(bad=bad), self.assertRaises(d.DistillError):
                d.cmd_nominate(self.root, bad, "x", actor="t")

    def test_double_nominate_refused(self):
        self._nominate()
        with self.assertRaises(d.DistillError):
            d.cmd_nominate(self.root, self.page_rel, "again", actor="t")


class TestDecide(DistillCase):
    def test_decide_binds_previous_event(self):
        did = self._nominate()
        head = self._last("nominated")
        self.assertEqual(self._accept(did), 0)   # merge 3 A: accepted は proposal / effect contract を要求する
        ev = self._last("decision")
        self.assertEqual(ev["event_type"], "decision")
        self.assertEqual(ev["expected_previous_state"], "nominated")
        self.assertEqual(ev["new_state"], "accepted")
        self.assertEqual(ev["previous_event_id"], head["event_id"])
        self.assertEqual(ev["previous_event_sha256"], head["_sha256"])

    def test_decide_requires_reason(self):
        did = self._nominate()
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did, "held", "   ", actor="t")

    def test_decide_from_wrong_state_refused(self):
        did = self._nominate()
        self._accept(did, "ok")
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_decide(self.root, did, "held", "again", actor="t")
        self.assertIn("accepted", str(cm.exception))

    def test_held_can_be_renominated(self):
        did = self._nominate()
        d.cmd_decide(self.root, did, "held", "後で", actor="t")
        self.assertEqual(d.state_head(self._events(), did)[0], "held")
        d.cmd_nominate(self.root, self.page_rel, "再開", actor="t")
        ev = d.state_chain(self._events(), did)[-1]      # chain の末尾＝再指名 event
        self.assertEqual(ev["expected_previous_state"], "held")
        self.assertEqual(ev["new_state"], "nominated")
        self.assertIn("previous_event_id", ev)      # absent 以外は previous 必須


class TestEventStore(DistillCase):
    def test_events_are_exclusive_create_and_immutable(self):
        did = self._nominate()
        p = d.events_dir(self.root) / f"{self._last('nominated')['event_id']}.json"
        ev = json.loads(p.read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        # 同じ event_id で書こうとしても既存を壊さず、別 ID へ退避する
        p2 = d.write_event(self.root, dict(ev, event_id=ev["event_id"]))
        self.assertNotEqual(p2.name, p.name)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), ev)

    def test_write_event_rejects_invalid_transition(self):
        did = self._nominate()
        subj = self._last("nominated")["subject"]
        bad = d._base_event("nominated", subj, "system", "t", reason="r")   # source が human でない
        bad.update(expected_previous_state="absent", new_state="nominated")
        with self.assertRaises(d.DistillError):
            d.write_event(self.root, bad)

    def test_state_events_require_page_subject(self):
        bad = d._base_event("nominated", {"subject_type": "task", "task_id": "x"}, "human", "t", reason="r")
        bad.update(expected_previous_state="absent", new_state="nominated")
        with self.assertRaises(d.DistillError):
            d.write_event(self.root, bad)

    def test_id_collision_retries_then_fails(self):
        did = self._nominate()
        subj = self._last("nominated")["subject"]
        fixed = d.new_event_id()
        # R5 (G17): retry は時刻 prefix を保ち token だけを引き直すようになったので、
        # 衝突の強制は new_event_id ではなく token 生成を固定して行う
        fixed_token = fixed.rsplit("-", 1)[-1]
        import secrets as _secrets
        original_token_hex = _secrets.token_hex            # 本物のモジュールを差し替えるので必ず戻す
        self.addCleanup(setattr, _secrets, "token_hex", original_token_hex)
        _secrets.token_hex = lambda n=4: fixed_token         # 常に同じ token（衝突を強制）
        try:
            ev = d._base_event("decision", subj, "human", "t", reason="r")
            ev.update(event_id=fixed, expected_previous_state="nominated", new_state="held",
                      previous_event_id=self._last("nominated")["event_id"],
                      previous_event_sha256=self._last("nominated")["_sha256"])
            d.write_event(self.root, ev)                     # 1本目は書ける
            with self.assertRaises(d.DistillError) as cm:
                d.write_event(self.root, dict(ev))           # 2本目は retries 使い切って失敗
            self.assertIn("collision", str(cm.exception))
        finally:
            import importlib
            importlib.reload(d)


class TestNote(DistillCase):
    def test_opportunity_then_terminal(self):
        did = self._nominate()
        self.assertEqual(d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None,
                                    trigger_source="scheduled", trigger_ref="run-1", opportunity_id=None,
                                    block_kind=None, source="host-task", strength="observed", reason=None,
                                    task_metadata={"cron": "0 10 23 * *"}, unverifiable_reason=None, actor="host"), 0)
        opp = self._last("opportunity")
        self.assertEqual(opp["trigger"]["task_metadata_status"], "snapshot")
        self.assertEqual(d.cmd_note(self.root, "completed", distill_id=did, task_id=None,
                                    trigger_source="scheduled", trigger_ref=None,
                                    opportunity_id=opp["opportunity_id"], block_kind=None,
                                    source="host-task", strength="observed", reason=None,
                                    task_metadata=None, unverifiable_reason=None, actor="host"), 0)
        self.assertEqual(self._last("completed")["opportunity_id"], opp["opportunity_id"])

    def test_unverifiable_metadata_requires_reason_field(self):
        did = self._nominate()
        d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                   trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None,
                   unverifiable_reason=None, actor="host")
        trig = self._last("opportunity")["trigger"]
        self.assertEqual(trig["task_metadata_status"], "unverifiable")
        self.assertTrue(trig["unverifiable_reason"])
        self.assertNotIn("task_metadata", trig)

    def test_blocked_requires_block_kind(self):
        did = self._nominate()
        d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                   trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        oid = self._last("opportunity")["opportunity_id"]
        with self.assertRaises(d.DistillError):
            d.cmd_note(self.root, "blocked", distill_id=did, task_id=None, trigger_source="scheduled",
                       trigger_ref=None, opportunity_id=oid, block_kind=None, source="host-task",
                       strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")

    def test_task_discovery_is_not_a_candidate_state(self):
        d.cmd_note(self.root, "opportunity", distill_id=None, task_id="monthly-listing-recon-csv",
                   trigger_source="scheduled", trigger_ref="r1", opportunity_id=None, block_kind=None,
                   source="host-task", strength="observed", reason=None, task_metadata=None,
                   unverifiable_reason=None, actor="host")
        ev = self._last("opportunity")
        self.assertEqual(ev["subject"]["subject_type"], "task")
        self.assertEqual(d.candidate_states(self._events()), {})   # candidate state は増えない

    def test_terminal_requires_opportunity_id(self):
        did = self._nominate()
        with self.assertRaises(d.DistillError):
            d.cmd_note(self.root, "completed", distill_id=did, task_id=None, trigger_source="scheduled",
                       trigger_ref=None, opportunity_id=None, block_kind=None, source="host-task",
                       strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")

    def test_deterministic_trigger_ref_is_stable(self):
        a = d.derive_trigger_ref("t1", "2026-09-05T06:02:35Z", "claude")
        b = d.derive_trigger_ref("t1", "2026-09-05T06:02:59Z", "claude")   # 同一分
        c = d.derive_trigger_ref("t1", "2026-09-05T06:03:00Z", "claude")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertRegex(a, r"^[0-9a-f]{16}$")


class TestThreshold(DistillCase):
    def _opp(self, did, ref, strength="observed"):
        d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                   trigger_ref=ref, opportunity_id=None, block_kind=None, source="host-task",
                   strength=strength, reason=None, task_metadata=None, unverifiable_reason=None, actor="h")

    def test_dedupe_and_strength_filter(self):
        did = self._nominate()
        self._opp(did, "r1")
        self._opp(did, "r1")            # 同じ dedupe key → 1件
        self._opp(did, "r2", strength="unverifiable")   # 算入しない
        counts = d.opportunity_counts(self._events())
        self.assertEqual(len(counts.get(did, [])), 1)
        self._opp(did, "r3")
        self.assertEqual(len(d.opportunity_counts(self._events()).get(did, [])), 2)

    def test_window_excludes_old_events(self):
        did = self._nominate()
        self._opp(did, "r1")
        import datetime
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=40)
        self.assertEqual(d.opportunity_counts(self._events(), window_days=30, now=future), {})


class TestIndexAndValidate(DistillCase):
    def test_reindex_is_deterministic_and_validate_ok(self):
        did = self._nominate()
        p = d.distill_dir(self.root) / "_index.md"
        first = p.read_text(encoding="utf-8")
        d.cmd_reindex(self.root)
        self.assertEqual(p.read_text(encoding="utf-8"), first)
        self.assertIn(did, first)
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_validate_detects_hand_edited_index(self):
        self._nominate()
        p = d.distill_dir(self.root) / "_index.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n手書き追記\n", encoding="utf-8")
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_validate_detects_broken_previous_hash(self):
        did = self._nominate()
        d.cmd_decide(self.root, did, "held", "r", actor="t")
        ev = self._last("decision")
        p = d.events_dir(self.root) / f"{ev['event_id']}.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["previous_event_sha256"] = "0" * 64
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_validate_detects_double_terminal(self):
        """CLI は2個目の terminal を拒否する（R5）ので、validator の検査は store を直接壊して行う。"""
        did = self._nominate()
        d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                   trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        oid = self._last("opportunity")["opportunity_id"]
        subj = self._last("opportunity")["subject"]
        d.cmd_note(self.root, "completed", distill_id=did, task_id=None, trigger_source="scheduled",
                   trigger_ref=None, opportunity_id=oid, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        rogue = d._base_event("blocked", dict(subj), "host-task", "h", strength="observed")
        rogue.update(opportunity_id=oid, block_kind="input_missing")
        d.write_event(self.root, rogue)          # CLI を迂回して壊す
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_validate_detects_missing_page(self):
        self._nominate()
        self.page.unlink()
        self.assertEqual(d.cmd_validate(self.root), 2)


class TestLockAndConcurrency(DistillCase):
    def test_state_change_requires_lock(self):
        did = self._nominate()
        (self.root / ".lock").mkdir()
        (self.root / ".lock" / "owner.json").write_text('{"token":"other"}', encoding="utf-8")
        try:
            with self.assertRaises(w.LockTimeout):
                d.cmd_decide(self.root, did, "held", "r", actor="t")
        finally:
            import shutil
            shutil.rmtree(self.root / ".lock")
        self.assertEqual(d.state_head(self._events(), did)[0], "nominated")   # 状態は変わらない

    def test_concurrent_decide_only_one_wins(self):
        did = self._nominate()
        results, errors = [], []

        self._write_proposal(did)   # merge 3 A: どちらのスレッドも成立し得る状態にしてから競わせる

        def go(state):
            try:
                results.append(d.cmd_decide(self.root, did, state, f"concurrent {state}", actor="t"))
            except BaseException as e:  # noqa: BLE001
                errors.append(type(e).__name__)
        ts = [threading.Thread(target=go, args=(s,)) for s in ("accepted", "held")]
        for t in ts:
            t.start()
        for t in ts:
            t.join(20)
        self.assertEqual(len(results), 1, f"1つだけ成功するはず: results={results} errors={errors}")
        self.assertEqual(len(errors), 1)
        evs = [e for e in self._events() if e["event_type"] == "decision"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_atomic_page_write_keeps_body(self):
        before = self.page.read_text(encoding="utf-8")
        self._nominate()
        after = self.page.read_text(encoding="utf-8")
        self.assertIn("# 手順", after)
        self.assertEqual(after.count("---"), before.count("---"))
        self.assertFalse(list(self.page.parent.glob(".tmp-*")))


class TestResolver(DistillCase):
    def test_portable_paths_accepted(self):
        for good in ("wiki/concepts/X.md", "raw/2026-09-05-note.md", "日本語/空白 あり.md"):
            (self.root / good).parent.mkdir(parents=True, exist_ok=True)
            (self.root / good).write_text("x", encoding="utf-8")
            self.assertTrue(d.resolve_under_base(self.root, good).is_file(), good)

    def test_lexical_escapes_refused(self):
        for bad in ("../x", "a/../../x", "./x", "a//b", "a/", "~/x", "/abs", "C:/x", "a\\b", "a\x7fb"):
            with self.subTest(bad=bad), self.assertRaises(d.DistillError):
                d.resolve_under_base(self.root, bad)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_symlink_escape_refused(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("s", encoding="utf-8")
        link = self.root / "wiki" / "escape"
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted")
        with self.assertRaises(d.DistillError):
            d.resolve_under_base(self.root, "wiki/escape/secret.md")


class TestLoadDiagnostics(DistillCase):
    """R1: 壊れた event file を validator から不可視にしない。"""

    def _write_raw(self, name, raw: bytes):
        d.events_dir(self.root).mkdir(parents=True, exist_ok=True)
        (d.events_dir(self.root) / name).write_bytes(raw)

    def test_invalid_json_is_reported(self):
        self._nominate()
        self._write_raw("20260101T000000Z-deadbeef.json", b"{")
        events, problems = d.scan_events(self.root)
        self.assertTrue(any("JSON" in p for p in problems), problems)
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_non_object_root_is_reported(self):
        self._nominate()
        self._write_raw("20260101T000000Z-deadbee0.json", b"[]")
        self.assertTrue(any("object" in p for p in d.scan_events(self.root)[1]))
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_filename_mismatch_is_reported(self):
        did = self._nominate()
        ev = self._last("nominated")
        src = d.events_dir(self.root) / f"{ev['event_id']}.json"
        src.rename(d.events_dir(self.root) / "20260101T000000Z-cafebabe.json")   # 改名＝id 不一致
        events, problems = d.scan_events(self.root)
        self.assertTrue(any("filename" in p for p in problems), problems)
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_empty_store_is_clean(self):
        events, problems = d.scan_events(self.root)
        self.assertEqual((events, problems), ([], []))
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_stray_file_is_reported(self):
        self._nominate()
        self._write_raw("notes.txt", b"hello")
        self.assertTrue(any("json" in p for p in d.scan_events(self.root)[1]))
        self.assertEqual(d.cmd_validate(self.root), 2)


class TestPageDrift(DistillCase):
    """R4: nominate 後に本文が変わったら validator が drift として fail-closed にする。"""

    def test_content_change_detected(self):
        self._nominate()
        self.assertEqual(d.cmd_validate(self.root), 0)
        self.page.write_text(self.page.read_text(encoding="utf-8") + "\n3. 追記\n", encoding="utf-8")
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_decide_rebinds_hash_and_clears_drift(self):
        did = self._nominate()
        self.page.write_text(self.page.read_text(encoding="utf-8") + "\n3. 追記\n", encoding="utf-8")
        self.assertEqual(d.cmd_validate(self.root), 2)
        d.cmd_decide(self.root, did, "held", "内容更新を確認した", actor="t")   # 決定時点の内容を束縛し直す
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_missing_page_detected(self):
        self._nominate()
        self.page.unlink()
        self.assertEqual(d.cmd_validate(self.root), 2)


class TestResolverOnStoredPaths(DistillCase):
    """R2: event に保存された page_path も、書き込み前に必ず resolver を通す。"""

    def _swap_dir_for_symlink(self):
        """nominate 後に concepts/ を Vault 外への symlink へ差し替える。"""
        import shutil
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir(exist_ok=True)
        real = self.root / "wiki" / "concepts"
        shutil.copy2(self.page, outside / self.page.name)
        shutil.rmtree(real)
        try:
            os.symlink(str(outside), str(real), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted")

    def test_decide_refuses_after_symlink_swap(self):
        did = self._nominate()
        self._swap_dir_for_symlink()
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        self.assertEqual(len([e for e in self._events() if e["event_type"] == "decision"]), 0)

    def test_note_refuses_after_symlink_swap(self):
        did = self._nominate()
        self._swap_dir_for_symlink()
        with self.assertRaises(d.DistillError):
            d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                       trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                       strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        self.assertEqual([e for e in self._events() if e["event_type"] == "opportunity"], [])


class TestNoteGuards(DistillCase):
    """R5: 先行 opportunity と terminal 重複は **書く前に** 拒否する。"""

    def _opp(self, did):
        d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                   trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        return self._last("opportunity")["opportunity_id"]

    def _terminal(self, did, oid, kind="completed", **kw):
        return d.cmd_note(self.root, kind, distill_id=did, task_id=None, trigger_source="scheduled",
                          trigger_ref=None, opportunity_id=oid, block_kind=kw.get("block_kind"),
                          source="host-task", strength="observed", reason=None, task_metadata=None,
                          unverifiable_reason=None, actor="h")

    def test_second_terminal_refused_without_writing(self):
        did = self._nominate()
        oid = self._opp(did)
        self._terminal(did, oid, "completed")
        before = len(self._events())
        with self.assertRaises(d.DistillError) as cm:
            self._terminal(did, oid, "blocked", block_kind="input_missing")
        self.assertIn("terminal", str(cm.exception))
        self.assertEqual(len(self._events()), before, "immutable store に2個目を書いてはいけない")
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_unknown_opportunity_refused(self):
        did = self._nominate()
        with self.assertRaises(d.DistillError):
            self._terminal(did, "op-20260101T000000Z-deadbeef", "completed")
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_invoked_then_completed_ok(self):
        did = self._nominate()
        oid = self._opp(did)
        self.assertEqual(self._terminal(did, oid, "invoked"), 0)
        self.assertEqual(self._terminal(did, oid, "completed"), 0)
        self.assertEqual(d.cmd_validate(self.root), 0)


class TestTriggerRefHost(DistillCase):
    """R6: dedupe key の host は evidence の source enum ではなく host identity。"""

    def test_different_hosts_do_not_collide(self):
        a = d.derive_trigger_ref("task-1", "2026-09-05T06:02:35Z", "PC-A")
        b = d.derive_trigger_ref("task-1", "2026-09-05T06:02:35Z", "PC-B")
        self.assertNotEqual(a, b)

    def test_cli_uses_host_identity_not_source(self):
        did = self._nominate()
        d.cmd_note(self.root, "opportunity", distill_id=did, task_id="t1", trigger_source="scheduled",
                   trigger_ref=None, opportunity_id=None, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None, unverifiable_reason=None,
                   actor="h", host="PC-A")
        ref_a = self._last("opportunity")["trigger"]["trigger_ref"]
        self.assertNotEqual(ref_a, d.derive_trigger_ref("t1", d.now_utc(), "host-task"))
        self.assertEqual(ref_a, d.derive_trigger_ref("t1", d.now_utc(), "PC-A"))

    def test_future_events_excluded_from_window(self):
        import datetime
        did = self._nominate()
        subj = self._last("nominated")["subject"]
        ev = d._base_event("opportunity", dict(subj), "host-task", "h", strength="observed")
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        ev.update(occurred_at=future.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  opportunity_id=d.new_opportunity_id(),
                  trigger={"trigger_source": "scheduled", "trigger_ref": "future",
                           "task_metadata_status": "unverifiable", "unverifiable_reason": "test"})
        d.write_event(self.root, ev)
        self.assertEqual(d.opportunity_counts(self._events()), {})


class TestLockOnEveryMutation(DistillCase):
    """R3: mutating verb は lock 中でだけ書く（standalone reindex も含む）。"""

    def _hold_lock(self):
        (self.root / ".lock").mkdir()
        (self.root / ".lock" / "owner.json").write_text('{"token":"other"}', encoding="utf-8")

    def _release(self):
        import shutil
        shutil.rmtree(self.root / ".lock", ignore_errors=True)

    def test_reindex_requires_lock(self):
        self._nominate()
        idx = d.distill_dir(self.root) / "_index.md"
        before = idx.read_text(encoding="utf-8")
        idx.unlink()
        self._hold_lock()
        try:
            with self.assertRaises(w.LockTimeout):
                d.cmd_reindex(self.root)
            self.assertFalse(idx.exists(), "lock を持たずに index を書いてはいけない")
        finally:
            self._release()
        d.cmd_reindex(self.root)
        self.assertEqual(idx.read_text(encoding="utf-8"), before)

    def test_note_requires_lock(self):
        did = self._nominate()
        self._hold_lock()
        try:
            with self.assertRaises(w.LockTimeout):
                d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                           trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                           strength="observed", reason=None, task_metadata=None,
                           unverifiable_reason=None, actor="h")
        finally:
            self._release()
        self.assertEqual([e for e in self._events() if e["event_type"] == "opportunity"], [])

    def test_nominate_requires_lock(self):
        p = self.root / "wiki" / "concepts" / "Second.md"
        p.write_text(PAGE, encoding="utf-8")
        self._hold_lock()
        try:
            with self.assertRaises(w.LockTimeout):
                d.cmd_nominate(self.root, "wiki/concepts/Second.md", "r", actor="t")
        finally:
            self._release()
        self.assertNotIn("distill_id", d.read_frontmatter(p))   # frontmatter も書かれない


class TestCorruptStoreBlocksMutation(DistillCase):
    """V2-R1: 破損した store の上に新しい event を積ませない。"""

    def _corrupt_last(self, event_type):
        ev = self._last(event_type)
        p = d.events_dir(self.root) / f"{ev['event_id']}.json"
        p.write_bytes(b"{")
        return ev

    def test_decide_refused_on_corrupt_store(self):
        did = self._nominate()
        self._accept(did, "ok")
        self._corrupt_last("decision")
        before = len(list(d.events_dir(self.root).glob("*.json")))
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_decide(self.root, did, "held", "壊れた後の追記", actor="t")
        self.assertIn("event store", str(cm.exception))
        self.assertEqual(len(list(d.events_dir(self.root).glob("*.json"))), before,
                         "破損 store に event を足してはいけない")

    def test_nominate_refused_on_corrupt_store(self):
        self._nominate()
        self._corrupt_last("nominated")
        p = self.root / "wiki" / "concepts" / "Second.md"
        p.write_text(PAGE, encoding="utf-8")
        before = len(list(d.events_dir(self.root).glob("*.json")))
        with self.assertRaises(d.DistillError):
            d.cmd_nominate(self.root, "wiki/concepts/Second.md", "r", actor="t")
        self.assertEqual(len(list(d.events_dir(self.root).glob("*.json"))), before)
        self.assertNotIn("distill_id", d.read_frontmatter(p))

    def test_note_refused_on_corrupt_store(self):
        did = self._nominate()
        self._corrupt_last("nominated")
        before = len(list(d.events_dir(self.root).glob("*.json")))
        with self.assertRaises(d.DistillError):
            d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                       trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                       strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        self.assertEqual(len(list(d.events_dir(self.root).glob("*.json"))), before)

    def test_status_warns_and_returns_nonzero(self):
        self._nominate()
        self._corrupt_last("nominated")
        self.assertEqual(d.cmd_status(self.root), 2)


class TestValidatorNeverRaises(DistillCase):
    """V2-R2: 任意の schema-invalid object でも例外を出さず rc=2 で全問題を列挙する。"""

    def _put(self, eid, payload):
        d.events_dir(self.root).mkdir(parents=True, exist_ok=True)
        (d.events_dir(self.root) / f"{eid}.json").write_text(json.dumps(payload, ensure_ascii=False),
                                                             encoding="utf-8")

    def test_opportunity_without_opportunity_id(self):
        self._nominate()
        self._put("20260101T000000Z-deadbeef",
                  {"event_id": "20260101T000000Z-deadbeef", "event_type": "opportunity"})
        self.assertEqual(d.cmd_validate(self.root), 2)      # KeyError にならない

    def test_terminal_without_opportunity_id(self):
        self._nominate()
        self._put("20260101T000000Z-deadbee1",
                  {"event_id": "20260101T000000Z-deadbee1", "event_type": "completed",
                   "occurred_at": "2026-01-01T00:00:00Z", "subject": {"subject_type": "task", "task_id": "t"},
                   "source": "host-task", "strength": "observed", "actor": "h"})
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_state_event_without_subject(self):
        self._nominate()
        self._put("20260101T000000Z-deadbee2",
                  {"event_id": "20260101T000000Z-deadbee2", "event_type": "decision", "new_state": "accepted"})
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_garbage_fields_only(self):
        self._nominate()
        self._put("20260101T000000Z-deadbee3", {"event_id": "20260101T000000Z-deadbee3", "x": [1, {"y": None}]})
        self.assertEqual(d.cmd_validate(self.root), 2)


class TestValidateEventIsTotal(DistillCase):
    """V4-R1: validate_event は任意の JSON object に対して例外を出さず problems を返す。"""

    def test_never_raises_on_arbitrary_objects(self):
        for obj in ({}, {"event_type": "nominated", "subject": "bad"},
                    {"event_type": "decision", "subject": [1, 2]},
                    {"event_type": "opportunity", "subject": None},
                    {"event_type": "completed", "opportunity_id": 5, "subject": {"subject_type": "task"}},
                    {"event_type": 42, "subject": {}}, {"x": "y"},
                    {"event_type": [], "subject": {}}, {"event_type": {"a": 1}, "subject": {}},
                    {"event_type": "nominated", "subject": {}, "occurred_at": 7}):
            with self.subTest(obj=obj):
                problems = d.validate_event(obj)     # 例外を出さない
                self.assertTrue(problems, f"問題を返すべき: {obj}")

    def test_non_dict_input(self):
        for obj in ("string", [1], 3, None):
            with self.subTest(obj=obj):
                self.assertTrue(d.validate_event(obj))

    def test_valid_event_has_no_problems(self):
        self._nominate()
        for ev in self._events():
            self.assertEqual(d.validate_event({k: v for k, v in ev.items() if not k.startswith("_")}), [], ev)


class TestSubjectIdentity(DistillCase):
    """V2-R3: subject の canonical identity を CLI guard と validator が同じ規則で使う。"""

    def _task_opp(self, task_id, ref):
        d.cmd_note(self.root, "opportunity", distill_id=None, task_id=task_id, trigger_source="scheduled",
                   trigger_ref=ref, opportunity_id=None, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        return self._last("opportunity")["opportunity_id"]

    def test_identity_tuples(self):
        self.assertEqual(d.subject_identity({"subject_type": "task", "task_id": "a"}), ("task", "a"))
        self.assertNotEqual(d.subject_identity({"subject_type": "task", "task_id": "a"}),
                            d.subject_identity({"subject_type": "task", "task_id": "b"}))
        page_a = {"subject_type": "page", "distill_id": "d-00000001", "page_path": "wiki/a.md", "page_sha256": "0" * 64}
        page_b = dict(page_a, page_sha256="1" * 64)
        self.assertEqual(d.subject_identity(page_a), d.subject_identity(page_b))   # hash は identity でない
        self.assertNotEqual(d.subject_identity(page_a), d.subject_identity(dict(page_a, page_path="wiki/b.md")))

    def test_cross_task_terminal_refused(self):
        oid_a = self._task_opp("task-A", "r1")
        self._task_opp("task-B", "r2")
        before = len(list(d.events_dir(self.root).glob("*.json")))
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_note(self.root, "completed", distill_id=None, task_id="task-B", trigger_source="scheduled",
                       trigger_ref=None, opportunity_id=oid_a, block_kind=None, source="host-task",
                       strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        self.assertIn("subject", str(cm.exception))
        self.assertEqual(len(list(d.events_dir(self.root).glob("*.json"))), before)

    def test_validator_detects_cross_subject_terminal(self):
        oid_a = self._task_opp("task-A", "r1")
        rogue = d._base_event("completed", {"subject_type": "task", "task_id": "task-B"}, "host-task", "h",
                              strength="observed")
        rogue["opportunity_id"] = oid_a
        d.write_event(self.root, rogue)          # CLI を迂回
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_cross_page_terminal_refused(self):
        did = self._nominate()
        d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                   trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        oid = self._last("opportunity")["opportunity_id"]
        with self.assertRaises(d.DistillError):
            d.cmd_note(self.root, "completed", distill_id=None, task_id="task-X", trigger_source="scheduled",
                       trigger_ref=None, opportunity_id=oid, block_kind=None, source="host-task",
                       strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")


class TestOpportunityIdUniqueness(DistillCase):
    """V2-R4: opportunity_id は全体で一意。CLI が書く前に拒否し、validator も検出する。"""

    def _opp(self, task_id, oid=None, ref="r"):
        return d.cmd_note(self.root, "opportunity", distill_id=None, task_id=task_id,
                          trigger_source="scheduled", trigger_ref=ref, opportunity_id=oid, block_kind=None,
                          source="host-task", strength="observed", reason=None, task_metadata=None,
                          unverifiable_reason=None, actor="h")

    def test_duplicate_id_refused_by_cli(self):
        oid = "op-20260101T000000Z-deadbeef"
        self.assertEqual(self._opp("task-A", oid, "r1"), 0)
        before = len(list(d.events_dir(self.root).glob("*.json")))
        with self.assertRaises(d.DistillError) as cm:
            self._opp("task-A", oid, "r2")
        self.assertIn("opportunity_id", str(cm.exception))
        self.assertEqual(len(list(d.events_dir(self.root).glob("*.json"))), before)

    def test_validator_detects_duplicate_id(self):
        oid = "op-20260101T000000Z-deadbeef"
        self._opp("task-A", oid, "r1")
        rogue = d._base_event("opportunity", {"subject_type": "task", "task_id": "task-A"}, "host-task", "h",
                              strength="observed")
        rogue.update(opportunity_id=oid,
                     trigger={"trigger_source": "scheduled", "trigger_ref": "r2",
                              "task_metadata_status": "unverifiable", "unverifiable_reason": "test"})
        d.write_event(self.root, rogue)          # CLI を迂回
        self.assertEqual(d.cmd_validate(self.root), 2)


class TestSchemaInvalidBlocksEverything(DistillCase):
    """V3-R1/V3-R2: 構文破損・filename 不整合・schema/遷移 不正のどれでも、
    すべての mutating verb（reindex を含む）がゼロ mutation で拒否し、status/validate は rc=2。"""

    BROKEN = {
        "syntax": (b"{", "20260101T000000Z-deadbee1"),
        "non_object": (b"[]", "20260101T000000Z-deadbee2"),
        "schema_invalid": (b'{"event_id":"20260101T000000Z-deadbee3","event_type":"opportunity"}',
                           "20260101T000000Z-deadbee3"),
        "event_type_is_list": (b'{"event_id":"20260101T000000Z-deadbee8","occurred_at":"2026-01-01T00:00:00Z",'
                               b'"event_type":[],"subject":{},"source":"human","strength":"observed","actor":"t"}',
                               "20260101T000000Z-deadbee8"),
        "event_type_is_object": (b'{"event_id":"20260101T000000Z-deadbee9","occurred_at":"2026-01-01T00:00:00Z",'
                                 b'"event_type":{"a":1},"subject":{},"source":"human","strength":"observed",'
                                 b'"actor":"t"}', "20260101T000000Z-deadbee9"),
        "occurred_at_is_number": (b'{"event_id":"20260101T000000Z-deadbeea","occurred_at":7,'
                                  b'"event_type":"opportunity","subject":{"subject_type":"task","task_id":"t"},'
                                  b'"source":"host-task","strength":"observed","actor":"h",'
                                  b'"opportunity_id":"op-20260101T000000Z-deadbeea"}',
                                  "20260101T000000Z-deadbeea"),
        "occurred_at_is_list": (b'{"event_id":"20260101T000000Z-deadbeeb","occurred_at":[1],'
                                b'"event_type":"opportunity","subject":{"subject_type":"task","task_id":"t"},'
                                b'"source":"host-task","strength":"observed","actor":"h",'
                                b'"opportunity_id":"op-20260101T000000Z-deadbeeb"}',
                                "20260101T000000Z-deadbeeb"),
        "subject_is_string": (b'{"event_id":"20260101T000000Z-deadbee5","occurred_at":"2026-01-01T00:00:00Z",'
                              b'"event_type":"nominated","subject":"bad","source":"human","strength":"observed",'
                              b'"actor":"t","reason":"r","expected_previous_state":"absent","new_state":"nominated"}',
                              "20260101T000000Z-deadbee5"),
        "subject_is_list": (b'{"event_id":"20260101T000000Z-deadbee6","occurred_at":"2026-01-01T00:00:00Z",'
                            b'"event_type":"decision","subject":[1,2],"source":"human","strength":"observed",'
                            b'"actor":"t","reason":"r","expected_previous_state":"nominated","new_state":"held"}',
                            "20260101T000000Z-deadbee6"),
        "new_state_missing": (b'{"event_id":"20260101T000000Z-deadbee7","occurred_at":"2026-01-01T00:00:00Z",'
                              b'"event_type":"nominated","subject":{"subject_type":"page","distill_id":"d-00000001",'
                              b'"page_path":"wiki/x.md","page_sha256":"' + b"a" * 64 + b'"},"source":"human",'
                              b'"strength":"observed","actor":"t","reason":"r",'
                              b'"expected_previous_state":"absent"}',
                              "20260101T000000Z-deadbee7"),
        "transition_invalid": (b'{"event_id":"20260101T000000Z-deadbee4","occurred_at":"2026-01-01T00:00:00Z",'
                               b'"event_type":"nominated","subject":{"subject_type":"page","distill_id":"d-00000001",'
                               b'"page_path":"wiki/x.md","page_sha256":"' + b"a" * 64 + b'"},"source":"human",'
                               b'"strength":"observed","actor":"t","reason":"r",'
                               b'"expected_previous_state":"accepted","new_state":"nominated"}',
                               "20260101T000000Z-deadbee4"),
    }

    def _break(self, kind):
        raw, eid = self.BROKEN[kind]
        d.events_dir(self.root).mkdir(parents=True, exist_ok=True)
        (d.events_dir(self.root) / f"{eid}.json").write_bytes(raw)

    def _filename_mismatch(self):
        ev = self._last("nominated")
        src = d.events_dir(self.root) / f"{ev['event_id']}.json"
        src.rename(d.events_dir(self.root) / "20260101T000000Z-cafebabe.json")

    def _snapshot(self):
        idx = d.distill_dir(self.root) / "_index.md"
        return (sorted(p.name for p in d.events_dir(self.root).glob("*.json")),
                idx.read_bytes() if idx.exists() else None,
                self.page.read_bytes())

    def _assert_all_verbs_refuse(self, did):
        before = self._snapshot()
        second = self.root / "wiki" / "concepts" / "Second.md"
        second.write_text(PAGE, encoding="utf-8")
        for label, fn in (
            ("nominate", lambda: d.cmd_nominate(self.root, "wiki/concepts/Second.md", "r", actor="t")),
            ("decide", lambda: d.cmd_decide(self.root, did, "accepted", "r", actor="t")),
            ("note", lambda: d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None,
                                        trigger_source="scheduled", trigger_ref="r1", opportunity_id=None,
                                        block_kind=None, source="host-task", strength="observed", reason=None,
                                        task_metadata=None, unverifiable_reason=None, actor="h")),
            ("reindex", lambda: d.cmd_reindex(self.root)),
        ):
            with self.subTest(verb=label):
                with self.assertRaises(d.DistillError):
                    fn()
        self.assertNotIn("distill_id", d.read_frontmatter(second))
        self.assertEqual(self._snapshot(), before, "破損 store では event/index/page が一切変わってはいけない")
        self.assertEqual(d.cmd_status(self.root), 2)
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_each_breakage_blocks_all_verbs(self):
        for kind in self.BROKEN:
            with self.subTest(kind=kind):
                self.setUp()
                try:
                    did = self._nominate()
                    self._break(kind)
                    self._assert_all_verbs_refuse(did)
                finally:
                    self.tearDown()
        self.setUp()   # tearDown が最後に呼ばれるので整合させる

    def test_filename_mismatch_blocks_all_verbs(self):
        did = self._nominate()
        self._filename_mismatch()
        before = self._snapshot()
        with self.assertRaises(d.DistillError):
            d.cmd_reindex(self.root)
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did, "held", "r", actor="t")
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(d.cmd_status(self.root), 2)

    def test_missing_index_stays_missing_on_broken_store(self):
        self._nominate()
        idx = d.distill_dir(self.root) / "_index.md"
        idx.unlink()
        self._break("schema_invalid")
        with self.assertRaises(d.DistillError):
            d.cmd_reindex(self.root)
        self.assertFalse(idx.exists(), "破損 store の valid subset で index を作り直さない")

    def test_mixed_occurred_at_types_do_not_break_sorting(self):
        """正常 event（string）と型違い（number/list）が共存しても sort/診断が落ちない（V5-R1）。"""
        did = self._nominate()
        self._break("occurred_at_is_number")
        self._break("occurred_at_is_list")
        before = self._snapshot()
        events, problems = d.store_health(self.root)      # 例外を出さない
        self.assertTrue(problems)
        self.assertEqual(len(events), 2, "正常な2件だけが validated として返る")
        self.assertEqual(d.cmd_status(self.root), 2)
        self.assertEqual(d.cmd_validate(self.root), 2)
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        self.assertEqual(self._snapshot(), before)

    def test_healthy_store_still_works(self):
        did = self._nominate()
        self.assertEqual(d.cmd_status(self.root), 0)
        self.assertEqual(d.cmd_validate(self.root), 0)
        idx = d.distill_dir(self.root) / "_index.md"
        idx.unlink()
        d.cmd_reindex(self.root)
        self.assertTrue(idx.exists())
        self.assertEqual(self._accept(did, "ok"), 0)


class _NoJsonschema:
    """jsonschema を import 不能にする context manager（V6-R1 の検証用）。"""

    def __enter__(self):
        import builtins
        self._real = builtins.__import__

        def fake(name, *a, **kw):
            if name == "jsonschema" or name.startswith("jsonschema."):
                raise ImportError("forced: jsonschema unavailable")
            return self._real(name, *a, **kw)
        builtins.__import__ = fake
        return self

    def __exit__(self, *exc):
        import builtins
        builtins.__import__ = self._real


def _valid_nominated(page_sha="a" * 64):
    return {"event_id": "20260101T000000Z-deadbee0", "occurred_at": "2026-01-01T00:00:00Z",
            "event_type": "nominated",
            "subject": {"subject_type": "page", "distill_id": "d-00000001",
                        "page_path": "wiki/x.md", "page_sha256": page_sha},
            "source": "human", "strength": "observed", "actor": "t", "reason": "r",
            "expected_previous_state": "absent", "new_state": "nominated"}


def _bound_to(bundle=False):
    b = {"proposal": {"path": "distill/x/proposal.md", "sha256": "b" * 64},
         "effect_contract": {"path": "distill/x/effect-contract.json", "sha256": "c" * 64}}
    if bundle:
        b["candidate_bundle"] = {"sha256": "d" * 64}
    return b


def _valid_decision(new_state="accepted"):
    ev = {"event_id": "20260101T000000Z-deadbee2", "occurred_at": "2026-01-01T00:00:00Z",
          "event_type": "decision",
          "subject": {"subject_type": "page", "distill_id": "d-00000001",
                      "page_path": "wiki/x.md", "page_sha256": "a" * 64},
          "source": "human", "strength": "observed", "actor": "t", "reason": "r",
          "expected_previous_state": "nominated", "new_state": new_state,
          "previous_event_id": "20260101T000000Z-deadbee0", "previous_event_sha256": "e" * 64}
    if new_state == "accepted":
        ev["bound_to"] = _bound_to()
    return ev


def _valid_rereviewed(state="accepted"):
    ev = {"event_id": "20260101T000000Z-deadbee3", "occurred_at": "2026-01-01T00:00:00Z",
          "event_type": "rereviewed",
          "subject": {"subject_type": "page", "distill_id": "d-00000001",
                      "page_path": "wiki/x.md", "page_sha256": "a" * 64},
          "source": "human", "strength": "observed", "actor": "t", "reason": "人が再レビューした",
          "expected_previous_state": state, "new_state": state,
          "previous_event_id": "20260101T000000Z-deadbee0", "previous_event_sha256": "e" * 64}
    if state == "accepted":
        ev["bound_to"] = _bound_to()
    return ev


def _valid_opportunity():
    return {"event_id": "20260101T000000Z-deadbee1", "occurred_at": "2026-01-01T00:00:00Z",
            "event_type": "opportunity",
            "subject": {"subject_type": "task", "task_id": "t1"},
            "source": "host-task", "strength": "observed", "actor": "h",
            "opportunity_id": "op-20260101T000000Z-deadbee1",
            "trigger": {"trigger_source": "scheduled", "trigger_ref": "r1",
                        "task_metadata_status": "unverifiable", "unverifiable_reason": "no adapter"}}


class TestValidationWithoutJsonschema(DistillCase):
    """V6-R1: jsonschema 不在でも schema-invalid を healthy にしない（built-in validation が正本）。"""

    SINGLE_VIOLATIONS = {
        "occurred_at_number": lambda e: e.update(occurred_at=7),
        "occurred_at_list": lambda e: e.update(occurred_at=[1]),
        "occurred_at_object": lambda e: e.update(occurred_at={"a": 1}),
        "occurred_at_bad_format": lambda e: e.update(occurred_at="2026-01-01 00:00:00"),
        "unknown_event_type": lambda e: e.update(event_type="promoted"),
        "bad_source": lambda e: e.update(source="robot"),
        "bad_strength": lambda e: e.update(strength="very-sure"),
        "bad_event_id": lambda e: e.update(event_id="not-an-id"),
        "unknown_field": lambda e: e.update(extra="x"),
        "subject_missing_field": lambda e: e["subject"].pop("page_sha256"),
        "subject_extra_field": lambda e: e["subject"].update(task_id="t"),
        "bad_page_sha": lambda e: e["subject"].update(page_sha256="zz"),
        "bad_page_path": lambda e: e["subject"].update(page_path="../escape.md"),
        "state_without_reason": lambda e: e.pop("reason"),
        "absent_with_previous": lambda e: e.update(previous_event_id="20260101T000000Z-deadbeef",
                                                   previous_event_sha256="b" * 64),
    }
    OPP_VIOLATIONS = {
        "trigger_missing_ref": lambda e: e["trigger"].pop("trigger_ref"),
        "trigger_empty_ref": lambda e: e["trigger"].update(trigger_ref=""),
        "trigger_bad_source": lambda e: e["trigger"].update(trigger_source="whenever"),
        "snapshot_without_metadata": lambda e: (e["trigger"].update(task_metadata_status="snapshot"),
                                                e["trigger"].pop("unverifiable_reason")),
        "unverifiable_with_metadata": lambda e: e["trigger"].update(task_metadata={"a": 1}),
        "opportunity_without_id": lambda e: e.pop("opportunity_id"),
        "opportunity_with_system_source": lambda e: e.update(source="system"),
        "trigger_not_object": lambda e: e.update(trigger="x"),
    }
    # merge 3 A: accepted の hash 束縛（bound_to）— 型・必須・形式を built-in と schema の両方で閉じる
    DECISION_VIOLATIONS = {
        # R2-1: accepted_without_bound_to は **legacy**（valid）。VALID_LEGACY 側で等価性を縛る
        "accepted_without_proposal": lambda e: e["bound_to"].pop("proposal"),
        "accepted_without_effect_contract": lambda e: e["bound_to"].pop("effect_contract"),
        "bound_to_not_object": lambda e: e.update(bound_to="x"),
        "bound_to_list": lambda e: e.update(bound_to=[1]),
        "bound_to_null": lambda e: e.update(bound_to=None),
        "bound_to_unknown_key": lambda e: e["bound_to"].update(runtime={"sha256": "a" * 64}),
        "proposal_not_object": lambda e: e["bound_to"].update(proposal="x"),
        "proposal_without_path": lambda e: e["bound_to"]["proposal"].pop("path"),
        "proposal_without_sha": lambda e: e["bound_to"]["proposal"].pop("sha256"),
        "proposal_bad_sha": lambda e: e["bound_to"]["proposal"].update(sha256="ZZ" + "a" * 62),
        "proposal_short_sha": lambda e: e["bound_to"]["proposal"].update(sha256="ab"),
        "proposal_sha_not_str": lambda e: e["bound_to"]["proposal"].update(sha256=7),
        "proposal_bad_path": lambda e: e["bound_to"]["proposal"].update(path="../escape.md"),
        "proposal_abs_path": lambda e: e["bound_to"]["proposal"].update(path="C:/x/proposal.md"),
        "proposal_extra_key": lambda e: e["bound_to"]["proposal"].update(role="wiki-page"),
        "bundle_with_path": lambda e: e["bound_to"].update(candidate_bundle={"path": "a/b", "sha256": "d" * 64}),
        "bundle_bad_sha": lambda e: e["bound_to"].update(candidate_bundle={"sha256": "d"}),
        "bundle_not_object": lambda e: e["bound_to"].update(candidate_bundle=True),
        "held_with_bound_to": lambda e: (e.update(new_state="held"),),
        "decision_from_accepted": lambda e: e.update(expected_previous_state="accepted"),
    }
    REREVIEW_VIOLATIONS = {
        "state_changed": lambda e: e.update(new_state="nominated"),
        "from_absent": lambda e: e.update(expected_previous_state="absent", new_state="absent"),
        "from_held": lambda e: e.update(expected_previous_state="held", new_state="held"),
        "from_rejected": lambda e: e.update(expected_previous_state="rejected", new_state="rejected"),
        "without_previous": lambda e: (e.pop("previous_event_id"), e.pop("previous_event_sha256")),
        "without_reason": lambda e: e.pop("reason"),
        "system_source": lambda e: e.update(source="system"),
        "task_subject": lambda e: e.update(subject={"subject_type": "task", "task_id": "t"}),
        "with_threshold": lambda e: e.update(threshold={"window_days": 30, "min_opportunities": 3,
                                                        "counted_event_ids": ["20260101T000000Z-deadbeef"]}),
    }

    def test_legacy_accepted_without_bound_to_is_valid(self):
        """R2-1: merge 3 より前に書かれた accepted（bound_to 無し）は built-in で通ること。

        event は append-only で直せない。過去に正しく書かれた event を今日から不正にしない。
        """
        for base in (_valid_decision, _valid_rereviewed):
            with self.subTest(event=base.__name__):
                ev = base()
                ev.pop("bound_to")
                with _NoJsonschema():
                    self.assertEqual(d.validate_event(ev), [])

    def test_single_violations_detected_without_jsonschema(self):
        with _NoJsonschema():
            self.assertEqual(d.validate_event(_valid_nominated()), [], "正常 event は問題なし")
            self.assertEqual(d.validate_event(_valid_opportunity()), [])
            self.assertEqual(d.validate_event(_valid_decision()), [])
            self.assertEqual(d.validate_event(_valid_decision("held")), [])
            self.assertEqual(d.validate_event(_valid_rereviewed()), [])
            self.assertEqual(d.validate_event(_valid_rereviewed("nominated")), [])
            for base, cases in ((_valid_nominated, self.SINGLE_VIOLATIONS),
                                (_valid_opportunity, self.OPP_VIOLATIONS),
                                (_valid_decision, self.DECISION_VIOLATIONS),
                                (_valid_rereviewed, self.REREVIEW_VIOLATIONS)):
                for name, mutate in cases.items():
                    with self.subTest(case=name):
                        ev = base()
                        mutate(ev)
                        self.assertTrue(d.validate_event(ev), f"{name} を検出できていない")

    def test_store_gate_without_jsonschema(self):
        """jsonschema 不在で、occurred_at だけが不正な event を置いても全 verb がゼロ mutation で拒否する。"""
        did = self._nominate()
        ev = _valid_opportunity()
        ev.update(event_id="20260101T000000Z-deadbeec", occurred_at=7,
                  opportunity_id="op-20260101T000000Z-deadbeec")
        (d.events_dir(self.root) / "20260101T000000Z-deadbeec.json").write_text(
            json.dumps(ev, ensure_ascii=False), encoding="utf-8")
        idx = d.distill_dir(self.root) / "_index.md"
        before = (sorted(p.name for p in d.events_dir(self.root).glob("*.json")),
                  idx.read_bytes(), self.page.read_bytes())
        with _NoJsonschema():
            events, problems = d.store_health(self.root)
            self.assertTrue(problems, "jsonschema 不在でも problems を返すこと")
            self.assertEqual(len(events), 2)
            self.assertEqual(d.cmd_status(self.root), 2)
            self.assertEqual(d.cmd_validate(self.root), 2)
            for label, fn in (("decide", lambda: d.cmd_decide(self.root, did, "accepted", "r", actor="t")),
                              ("reindex", lambda: d.cmd_reindex(self.root)),
                              ("note", lambda: d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None,
                                                          trigger_source="scheduled", trigger_ref="r9",
                                                          opportunity_id=None, block_kind=None, source="host-task",
                                                          strength="observed", reason=None, task_metadata=None,
                                                          unverifiable_reason=None, actor="h"))):
                with self.subTest(verb=label), self.assertRaises(d.DistillError):
                    fn()
        after = (sorted(p.name for p in d.events_dir(self.root).glob("*.json")),
                 idx.read_bytes(), self.page.read_bytes())
        self.assertEqual(after, before)

    def test_healthy_store_without_jsonschema_still_works(self):
        did = self._nominate()
        self._write_proposal(did)
        with _NoJsonschema():
            self.assertEqual(d.cmd_status(self.root), 0)
            self.assertEqual(d.cmd_validate(self.root), 0)
            self.assertEqual(d.cmd_decide(self.root, did, "accepted", "ok", actor="t"), 0)


@unittest.skipIf(__import__("importlib").util.find_spec("jsonschema") is None, "jsonschema not installed")
class TestBuiltinMatchesSchema(DistillCase):
    """built-in validation と jsonschema の判定が一致すること（drift 検出）。"""

    def _corpus(self):
        out = [("valid_nominated", _valid_nominated()), ("valid_opportunity", _valid_opportunity()),
               ("valid_decision_accepted", _valid_decision()), ("valid_decision_held", _valid_decision("held")),
               ("valid_decision_rejected", _valid_decision("rejected")),
               ("valid_decision_bundle", dict(_valid_decision(), bound_to=_bound_to(bundle=True))),
               ("valid_rereviewed_accepted", _valid_rereviewed()),
               ("valid_rereviewed_nominated", _valid_rereviewed("nominated")),
               ("legacy_decision_without_bound_to",
                {k: v for k, v in _valid_decision().items() if k != "bound_to"}),
               ("legacy_rereviewed_without_bound_to",
                {k: v for k, v in _valid_rereviewed().items() if k != "bound_to"})]
        T = TestValidationWithoutJsonschema
        for base, cases in ((_valid_nominated, T.SINGLE_VIOLATIONS), (_valid_opportunity, T.OPP_VIOLATIONS),
                            (_valid_decision, T.DECISION_VIOLATIONS), (_valid_rereviewed, T.REREVIEW_VIOLATIONS)):
            for name, mutate in cases.items():
                ev = base()
                mutate(ev)
                out.append((f"{base.__name__}:{name}", ev))
        # 新 field の型を再帰的に振る（片方だけが通す JSON 値を潰す）
        for i, junk in enumerate((None, True, 7, "x", [], {}, [{"sha256": "a" * 64}],
                                  {"proposal": None}, {"proposal": {"path": None, "sha256": "a" * 64}},
                                  {"proposal": {"path": "distill/x/p.md", "sha256": ["a" * 64]}},
                                  {"proposal": {"path": "distill/x/p.md", "sha256": "A" * 64},
                                   "effect_contract": {"path": "distill/x/e.json", "sha256": "c" * 64}},
                                  {"candidate_bundle": {"sha256": "d" * 64}})):
            out.append((f"bound_to_junk_{i}", dict(_valid_decision(), bound_to=junk)))
            out.append((f"rereview_bound_to_junk_{i}", dict(_valid_rereviewed(), bound_to=junk)))
        # 実際に生成される event も corpus に含める
        did = self._nominate()
        self._accept(did, "ok", bundle_sha256="f" * 64)
        d.cmd_rereview(self.root, did, "再レビュー", actor="t")
        for ev in self._events():
            out.append((f"generated_{ev['event_type']}", {k: v for k, v in ev.items() if not k.startswith("_")}))
        return out

    def test_builtin_and_schema_agree(self):
        from jsonschema import Draft202012Validator as V
        schema = json.loads((Path(d.SCHEMA_DIR) / "distill-event.schema.json").read_text(encoding="utf-8"))
        for name, ev in self._corpus():
            with self.subTest(case=name):
                builtin_ok = not d.builtin_validate_event(ev)
                schema_ok = not list(V(schema).iter_errors(ev))
                self.assertEqual(builtin_ok, schema_ok,
                                 f"{name}: built-in={builtin_ok} schema={schema_ok}（判定が食い違う）")


class TestLateSortingInvalidEvent(DistillCase):
    """V7-R1: **正常 event より後ろに並ぶ** schema-invalid event でも、
    mutating verb が health gate に到達して DistillError・ゼロ mutation になる。"""

    LATE = {
        "subject_string": b'{"event_id":"99990101T000000Z-deadbeef","occurred_at":"9999-01-01T00:00:00Z",'
                          b'"event_type":"nominated","subject":"bad","source":"human","strength":"observed",'
                          b'"actor":"t","reason":"r","expected_previous_state":"absent","new_state":"nominated"}',
        "subject_list": b'{"event_id":"99990101T000000Z-deadbee0","occurred_at":"9999-01-01T00:00:00Z",'
                        b'"event_type":"decision","subject":[1,2],"source":"human","strength":"observed",'
                        b'"actor":"t","reason":"r","expected_previous_state":"nominated","new_state":"held"}',
        "opportunity_subject_string": b'{"event_id":"99990101T000000Z-deadbee1","occurred_at":"9999-01-01T00:00:00Z",'
                                      b'"event_type":"opportunity","subject":"bad","source":"host-task",'
                                      b'"strength":"observed","actor":"h",'
                                      b'"opportunity_id":"op-99990101T000000Z-deadbee1",'
                                      b'"trigger":{"trigger_source":"scheduled","trigger_ref":"r",'
                                      b'"task_metadata_status":"unverifiable","unverifiable_reason":"x"}}',
    }

    def _place(self, kind):
        raw = self.LATE[kind]
        eid = json.loads(raw)["event_id"]
        (d.events_dir(self.root) / f"{eid}.json").write_bytes(raw)

    def _snapshot(self):
        idx = d.distill_dir(self.root) / "_index.md"
        return (sorted(p.name for p in d.events_dir(self.root).glob("*.json")),
                idx.read_bytes() if idx.exists() else None, self.page.read_bytes())

    def test_note_with_distill_id_refuses(self):
        """Codex 再現: reverse scan が先に不正 event へ当たる配置でも AttributeError にしない。"""
        for kind in self.LATE:
            with self.subTest(kind=kind):
                self.setUp()
                try:
                    did = self._nominate()
                    self._place(kind)
                    before = self._snapshot()
                    with self.assertRaises(d.DistillError):
                        d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None,
                                   trigger_source="scheduled", trigger_ref="r1", opportunity_id=None,
                                   block_kind=None, source="host-task", strength="observed", reason=None,
                                   task_metadata=None, unverifiable_reason=None, actor="h")
                    self.assertEqual(self._snapshot(), before)
                finally:
                    self.tearDown()
        self.setUp()

    def test_all_verbs_refuse_with_late_invalid_event(self):
        did = self._nominate()
        self._place("subject_string")
        before = self._snapshot()
        second = self.root / "wiki" / "concepts" / "Second.md"
        second.write_text(PAGE, encoding="utf-8")
        for label, fn in (
            ("nominate", lambda: d.cmd_nominate(self.root, "wiki/concepts/Second.md", "r", actor="t")),
            ("decide", lambda: d.cmd_decide(self.root, did, "accepted", "r", actor="t")),
            ("note-task", lambda: d.cmd_note(self.root, "opportunity", distill_id=None, task_id="t1",
                                             trigger_source="scheduled", trigger_ref="r1", opportunity_id=None,
                                             block_kind=None, source="host-task", strength="observed",
                                             reason=None, task_metadata=None, unverifiable_reason=None, actor="h")),
            ("reindex", lambda: d.cmd_reindex(self.root)),
        ):
            with self.subTest(verb=label), self.assertRaises(d.DistillError):
                fn()
        self.assertNotIn("distill_id", d.read_frontmatter(second))
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(d.cmd_status(self.root), 2)
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_note_reads_only_validated_events_for_subject(self):
        """subject 解決は validated events からのみ行う（不正 event を subject の材料にしない）。"""
        did = self._nominate()
        self._place("opportunity_subject_string")
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_note(self.root, "completed", distill_id=did, task_id=None, trigger_source="scheduled",
                       trigger_ref=None, opportunity_id="op-99990101T000000Z-deadbee1", block_kind=None,
                       source="host-task", strength="observed", reason=None, task_metadata=None,
                       unverifiable_reason=None, actor="h")
        self.assertIn("event store", str(cm.exception))   # health gate で止まる（先行 opportunity 検査より前）


class TestAcceptedBinding(DistillCase):
    """merge 3 A: `decide accepted` は proposal / effect contract / candidate bundle の hash を自動で束縛する。"""

    def _snapshot(self):
        return sorted(p.name for p in d.events_dir(self.root).glob("*.json"))

    def test_accepted_binds_proposal_and_contract(self):
        did = self._nominate()
        prop, ec = self._write_proposal(did)
        self.assertEqual(d.cmd_decide(self.root, did, "accepted", "sandbox 証拠", actor="t"), 0)
        bound = self._last("decision")["bound_to"]
        self.assertEqual(bound["proposal"], {"path": f"distill/{SLUG}/proposal.md",
                                             "sha256": d.sha256_file(prop)})
        self.assertEqual(bound["effect_contract"], {"path": f"distill/{SLUG}/effect-contract.json",
                                                    "sha256": d.sha256_file(ec)})
        self.assertNotIn("candidate_bundle", bound)
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_bundle_sha256_is_bound_when_given(self):
        did = self._nominate()
        self._write_proposal(did)
        d.cmd_decide(self.root, did, "accepted", "r", actor="t", bundle_sha256="a" * 64)
        self.assertEqual(self._last("decision")["bound_to"]["candidate_bundle"], {"sha256": "a" * 64})

    def test_bad_bundle_sha256_is_refused(self):
        did = self._nominate()
        self._write_proposal(did)
        before = self._snapshot()
        for bad in ("abc", "A" * 64, "a" * 63, "a" * 65, " " + "a" * 64):
            with self.subTest(bad=bad), self.assertRaises(d.DistillError):
                d.cmd_decide(self.root, did, "accepted", "r", actor="t", bundle_sha256=bad)
        with self.assertRaises(SystemExit):          # CLI は引数エラー（rc=2）
            d.main(["--wiki-root", str(self.root), "decide", did, "accepted",
                    "--reason", "r", "--bundle-sha256", "nope"])
        self.assertEqual(self._snapshot(), before)

    def test_bundle_sha256_refused_for_non_accepted(self):
        did = self._nominate()
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did, "held", "r", actor="t", bundle_sha256="a" * 64)
        self.assertEqual([e for e in self._events() if e["event_type"] == "decision"], [])

    def test_missing_proposal_writes_nothing(self):
        did = self._nominate()
        before = self._snapshot()
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        self.assertIn("proposal", str(cm.exception))
        self.assertEqual(self._snapshot(), before, "hash 無しの accepted を書いてはいけない")
        self.assertEqual(d.state_head(self._events(), did)[0], "nominated")

    def test_missing_effect_contract_writes_nothing(self):
        did = self._nominate()
        self._write_proposal(did)[1].unlink()
        before = self._snapshot()
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        self.assertIn("effect-contract", str(cm.exception))
        self.assertEqual(self._snapshot(), before)

    def test_broken_proposal_frontmatter_writes_nothing(self):
        did = self._nominate()
        prop, _ = self._write_proposal(did)
        prop.write_text("frontmatter がありません\n", encoding="utf-8")
        before = self._snapshot()
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        self.assertEqual(self._snapshot(), before)

    def test_proposal_pointing_at_other_candidate_is_not_used(self):
        """frontmatter の skill_slug が directory と食い違う proposal は使わない（別 candidate への偽装）。"""
        did = self._nominate()
        self._write_proposal(did, skill_slug="other-slug")
        before = self._snapshot()
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        self.assertEqual(self._snapshot(), before)

    def test_held_with_bound_to_is_rejected(self):
        did = self._nominate()
        head = self._last("nominated")
        ev = d._base_event("decision", dict(head["subject"]), "human", "t", reason="r")
        ev.update(expected_previous_state="nominated", new_state="held",
                  previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"],
                  bound_to={"proposal": {"path": "distill/x/proposal.md", "sha256": "a" * 64},
                            "effect_contract": {"path": "distill/x/effect-contract.json", "sha256": "b" * 64}})
        with self.assertRaises(d.DistillError):
            d.write_event(self.root, ev)                       # CLI 経路では書けない
        (d.events_dir(self.root) / f"{ev['event_id']}.json").write_text(
            json.dumps(ev, ensure_ascii=False), encoding="utf-8")   # 手で置いても validate が拒む
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_proposal_drift_fails_validate(self):
        did = self._nominate()
        prop, _ = self._write_proposal(did)
        d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        self.assertEqual(d.cmd_validate(self.root), 0)
        prop.write_text(prop.read_text(encoding="utf-8") + "\n追記\n", encoding="utf-8")
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_effect_contract_drift_fails_validate(self):
        did = self._nominate()
        _, ec = self._write_proposal(did)
        d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        ec.write_text(json.dumps(effect_contract(), ensure_ascii=False), encoding="utf-8")  # 順序違いで hash が変わる
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_bound_to_pointing_at_another_candidates_proposal_fails_validate(self):
        """CLI を迂回して別 candidate の proposal hash を束縛した accepted は、hash が合っていても FAIL。"""
        did = self._nominate()
        other = self.root / "wiki" / "concepts" / "Other.md"
        other.write_text(PAGE, encoding="utf-8")
        d.cmd_nominate(self.root, "wiki/concepts/Other.md", "r", actor="t")
        other_did = d.read_frontmatter(other)["distill_id"]
        prop, ec = self._write_proposal(other_did, slug="other-skill")   # 別 candidate の proposal
        head = [e for e in self._events()
                if e["event_type"] == "nominated" and e["subject"]["distill_id"] == did][-1]
        ev = d._base_event("decision", dict(head["subject"]), "human", "t", reason="r")
        ev.update(expected_previous_state="nominated", new_state="accepted",
                  previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"],
                  bound_to={"proposal": {"path": "distill/other-skill/proposal.md",
                                         "sha256": d.sha256_file(prop)},
                            "effect_contract": {"path": "distill/other-skill/effect-contract.json",
                                                "sha256": d.sha256_file(ec)}})
        d.write_event(self.root, ev)                    # schema 的には妥当（hash も実体と一致する）
        d.cmd_reindex(self.root)
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_deleted_proposal_fails_validate(self):
        did = self._nominate()
        prop, _ = self._write_proposal(did)
        d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        prop.unlink()
        self.assertEqual(d.cmd_validate(self.root), 2)   # 消えた proposal を「drift 無し」にしない


class TestRereview(DistillCase):
    """merge 3 B: 人の再レビューを1イベントで記録する（state を変えず page identity を束縛し直す）。"""

    def _edit_page(self):
        self.page.write_text(self.page.read_text(encoding="utf-8") + "\n3. 追記\n", encoding="utf-8")

    def test_rereview_from_nominated_keeps_state_and_rebinds_page(self):
        did = self._nominate()
        self._edit_page()
        self.assertEqual(d.cmd_validate(self.root), 2)          # page drift
        self.assertEqual(d.cmd_rereview(self.root, did, "本文を直したので再レビューした", actor="t"), 0)
        ev = d.state_chain(self._events(), did)[-1]
        self.assertEqual(ev["event_type"], "rereviewed")
        self.assertEqual((ev["expected_previous_state"], ev["new_state"]), ("nominated", "nominated"))
        self.assertEqual(ev["subject"]["page_sha256"], d.sha256_file(self.page))
        self.assertNotIn("bound_to", ev)
        self.assertEqual(d.state_head(self._events(), did)[0], "nominated")
        self.assertEqual(d.cmd_validate(self.root), 0)          # drift 判定は rereviewed を基準にする

    def test_rereview_from_accepted_rebinds_bound_to(self):
        did = self._nominate()
        prop, ec = self._write_proposal(did)
        d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        old = self._last("decision")["bound_to"]["proposal"]["sha256"]
        prop.write_text(prop.read_text(encoding="utf-8") + "\n改訂\n", encoding="utf-8")
        self._edit_page()
        self.assertEqual(d.cmd_validate(self.root), 2)          # page drift ＋ proposal drift
        self.assertEqual(d.cmd_rereview(self.root, did, "改訂版を人が再レビューした", actor="t"), 0)
        ev = d.state_chain(self._events(), did)[-1]
        self.assertEqual(ev["new_state"], "accepted")           # state は変わらない
        self.assertEqual(ev["subject"]["page_sha256"], d.sha256_file(self.page))
        self.assertNotEqual(ev["bound_to"]["proposal"]["sha256"], old)
        self.assertEqual(ev["bound_to"]["proposal"]["sha256"], d.sha256_file(prop))
        self.assertEqual(ev["bound_to"]["effect_contract"]["sha256"], d.sha256_file(ec))
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_rereview_can_rebind_candidate_bundle(self):
        did = self._nominate()
        self._write_proposal(did)
        d.cmd_decide(self.root, did, "accepted", "r", actor="t", bundle_sha256="a" * 64)
        d.cmd_rereview(self.root, did, "再ビルドを確認した", actor="t", bundle_sha256="b" * 64)
        ev = d.state_chain(self._events(), did)[-1]
        self.assertEqual(ev["bound_to"]["candidate_bundle"], {"sha256": "b" * 64})

    def test_rereview_refused_from_other_states(self):
        did = self._nominate()
        d.cmd_decide(self.root, did, "held", "後で", actor="t")
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_rereview(self.root, did, "r", actor="t")
        self.assertIn("held", str(cm.exception))
        self.assertEqual([e for e in self._events() if e["event_type"] == "rereviewed"], [])
        with self.assertRaises(d.DistillError):                # absent（未登録の distill_id）
            d.cmd_rereview(self.root, "d-00000001", "r", actor="t")

    def test_rereview_refused_after_rejected(self):
        did = self._nominate()
        d.cmd_decide(self.root, did, "rejected", "却下", actor="t")
        with self.assertRaises(d.DistillError):
            d.cmd_rereview(self.root, did, "r", actor="t")

    def test_rereview_requires_reason(self):
        did = self._nominate()
        with self.assertRaises(d.DistillError):
            d.cmd_rereview(self.root, did, "   ", actor="t")

    def test_rereview_writes_nothing_when_page_unreadable(self):
        did = self._nominate()
        self.page.unlink()
        before = sorted(p.name for p in d.events_dir(self.root).glob("*.json"))
        with self.assertRaises(d.DistillError):
            d.cmd_rereview(self.root, did, "r", actor="t")
        self.assertEqual(sorted(p.name for p in d.events_dir(self.root).glob("*.json")), before)

    def test_rereview_writes_nothing_when_proposal_missing(self):
        did = self._nominate()
        prop, _ = self._write_proposal(did)
        d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        prop.unlink()
        before = sorted(p.name for p in d.events_dir(self.root).glob("*.json"))
        with self.assertRaises(d.DistillError):
            d.cmd_rereview(self.root, did, "r", actor="t")
        self.assertEqual(sorted(p.name for p in d.events_dir(self.root).glob("*.json")), before)

    def test_rereview_requires_lock(self):
        did = self._nominate()
        (self.root / ".lock").mkdir()
        (self.root / ".lock" / "owner.json").write_text('{"token":"other"}', encoding="utf-8")
        try:
            with self.assertRaises(w.LockTimeout):
                d.cmd_rereview(self.root, did, "r", actor="t")
        finally:
            import shutil
            shutil.rmtree(self.root / ".lock", ignore_errors=True)
        self.assertEqual([e for e in self._events() if e["event_type"] == "rereviewed"], [])

    def test_rereview_refused_on_corrupt_store(self):
        did = self._nominate()
        ev = self._last("nominated")
        (d.events_dir(self.root) / f"{ev['event_id']}.json").write_bytes(b"{")
        with self.assertRaises(d.DistillError) as cm:
            d.cmd_rereview(self.root, did, "r", actor="t")
        self.assertIn("event store", str(cm.exception))

    def test_decide_after_rereview_binds_the_rereview_head(self):
        did = self._nominate()
        d.cmd_rereview(self.root, did, "再レビュー", actor="t")
        head = d.state_chain(self._events(), did)[-1]
        self._accept(did, "ok")
        dec = self._last("decision")
        self.assertEqual(dec["previous_event_id"], head["event_id"])
        self.assertEqual(d.cmd_validate(self.root), 0)


class TestValidateRefs(DistillCase):
    """merge 3 C: `validate --refs` は ok / mismatch / unverifiable の3値で数える。"""

    def setUp(self):
        super().setUp()
        self.outside = Path(self.tmp.name) / "ebay"
        (self.outside / "OpenLister").mkdir(parents=True)
        self.runtime = self.outside / "OpenLister" / "runner.py"
        self.runtime.write_text("print(1)\n", encoding="utf-8")

    def _refs(self, *extra):
        return [{"path": self.page_rel, "sha256": d.sha256_file(self.page), "role": "wiki-page"}] + list(extra)

    def _runtime_ref(self, sha=None):
        return {"path": "eBay/OpenLister/runner.py", "sha256": sha or d.sha256_file(self.runtime),
                "role": "runtime"}

    def _validate(self, **kw):
        return d.cmd_validate(self.root, refs=True, **kw)

    def test_all_ok(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=self._refs(self._runtime_ref()))
        self._accept_existing(did)
        self.assertEqual(self._validate(ref_bases={"eBay": str(self.outside)}), 0)

    def _accept_existing(self, did):
        """既に書いた proposal を消さずに accepted まで進める。"""
        return d.cmd_decide(self.root, did, "accepted", "r", actor="t")

    def test_source_ref_mismatch_fails(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=self._refs(self._runtime_ref(sha="a" * 64)))
        self._accept_existing(did)
        self.assertEqual(self._validate(ref_bases={"eBay": str(self.outside)}), 2)

    def test_wiki_page_ref_mismatch_fails(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=[{"path": self.page_rel, "sha256": "a" * 64,
                                                "role": "wiki-page"}])
        self._accept_existing(did)
        self.assertEqual(self._validate(), 2)

    def test_missing_file_is_mismatch(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=self._refs(
            {"path": "wiki/concepts/NotHere.md", "sha256": "a" * 64, "role": "wiki-page"}))
        self._accept_existing(did)
        self.assertEqual(self._validate(), 2)

    def test_base_not_given_is_unverifiable_not_fail(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=self._refs(self._runtime_ref(sha="a" * 64)))
        self._accept_existing(did)
        self.assertEqual(self._validate(), 0, "base 未指定は unverifiable（FAIL にしない）")
        self.assertEqual(self._validate(ref_bases={"eBay": str(self.outside)}), 2, "base を与えたら照合する")

    def test_unknown_base_dir_is_unverifiable(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=self._refs(self._runtime_ref(sha="a" * 64)))
        self._accept_existing(did)
        self.assertEqual(self._validate(ref_bases={"eBay": str(self.outside / "no-such-dir")}), 0)

    def test_counts_are_reported(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=self._refs(
            self._runtime_ref(), {"path": "other/z.py", "sha256": "a" * 64, "role": "other"}))
        self._accept_existing(did)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self._validate(ref_bases={"eBay": str(self.outside)})
        self.assertEqual(rc, 0)
        self.assertIn("ok=3 mismatch=0 unverifiable=1", buf.getvalue())
        self.assertIn("runtime", buf.getvalue(), "role は報告に載せる")

    def test_containment_violation_is_unverifiable(self):
        """base 配下から出る ref（symlink 経由・`..`）は unverifiable であり ok ではない。"""
        did = self._nominate()
        escape = {"path": "eBay/escape/secret.txt", "sha256": "a" * 64, "role": "runtime"}
        secret = Path(self.tmp.name) / "secret"
        secret.mkdir()
        (secret / "secret.txt").write_text("s", encoding="utf-8")
        try:
            os.symlink(str(secret), str(self.outside / "escape"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted")
        self._write_proposal(did, source_refs=self._refs(escape))
        self._accept_existing(did)
        self.assertEqual(self._validate(ref_bases={"eBay": str(self.outside)}), 0)   # FAIL でも ok でもない
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._validate(ref_bases={"eBay": str(self.outside)})
        self.assertIn("unverifiable=1", buf.getvalue())

    def test_non_portable_ref_path_is_unverifiable(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=self._refs(
            {"path": "../outside.md", "sha256": "a" * 64, "role": "other"}))
        self._accept_existing(did)
        self.assertEqual(self._validate(), 0)

    def test_refs_off_by_default(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=self._refs(
            {"path": self.page_rel, "sha256": "a" * 64, "role": "wiki-page"}))
        self._accept_existing(did)
        self.assertEqual(d.cmd_validate(self.root), 0, "--refs 無しでは実体照合しない")
        self.assertEqual(self._validate(), 2)

    def test_ref_base_argument_parsing(self):
        self.assertEqual(d._ref_base_arg("eBay=I:/Workspace/eBay"), ("eBay", "I:/Workspace/eBay"))
        import argparse
        for bad in ("eBay", "=dir", "eBay=", "e/Bay=dir", ""):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                d._ref_base_arg(bad)


class TestProposalAndContractFields(DistillCase):
    """merge 3 D / E: 後方互換 field（extensions）と top-level field の食い違いを validate が報告する。"""

    def test_document_revision_agrees(self):
        did = self._nominate()
        self._write_proposal(did, extra_lines=["document_revision: 1.2", "supersedes: 1.1",
                                               'extensions: {"revision": "1.2"}'])
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_document_revision_conflict_reported(self):
        did = self._nominate()
        self._write_proposal(did, extra_lines=["document_revision: 1.2",
                                               'extensions: {"revision": "1.1"}'])
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_expected_inputs_agrees_with_legacy_extension(self):
        did = self._nominate()
        self._write_proposal(did, contract=effect_contract(
            expected_inputs={"accounts": ["main", "sub"]},
            extensions={"expected-accounts": ["main", "sub"]}))
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_expected_inputs_conflict_reported(self):
        did = self._nominate()
        self._write_proposal(did, contract=effect_contract(
            expected_inputs={"accounts": ["main"]},
            extensions={"expected-accounts": ["main", "sub"]}))
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_broken_effect_contract_json_reported(self):
        did = self._nominate()
        _, ec = self._write_proposal(did)
        ec.write_text("{", encoding="utf-8")
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_proposal_frontmatter_unparseable_reported(self):
        """R2-2: 読めない frontmatter は FAIL（黙って部分的に読まない）。

        R1 はここで `{"path"` → `{path` を「JSON として読めない」と扱っていたが、それは YAML flow
        としては**妥当**なので今は読める。「読めない」の例は行内で閉じないクォートに差し替えた。
        """
        did = self._nominate()
        prop, _ = self._write_proposal(did)
        text = prop.read_text(encoding="utf-8").replace('effect_contract: {"path"', 'effect_contract: {"path')
        prop.write_text(text, encoding="utf-8")
        self.assertEqual(d.cmd_validate(self.root), 2)
        records, problems = d.scan_proposals(self.root)
        self.assertTrue(any(d.UNPARSEABLE_PREFIX in x for x in problems), problems)
        self.assertTrue(records[did]["unparseable"])

    def test_proposal_frontmatter_flow_mapping_with_plain_key_is_read(self):
        """`{path: "x", "sha256": "y"}` は YAML flow として妥当なので読める（R1 は落としていた）。"""
        did = self._nominate()
        prop, ec = self._write_proposal(did)
        text = prop.read_text(encoding="utf-8").replace('effect_contract: {"path"', "effect_contract: {path")
        prop.write_text(text, encoding="utf-8")
        records, problems = d.scan_proposals(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(records[did]["fm"]["effect_contract"]["sha256"], d.sha256_file(ec))


# ---------------------------------------------------------------------------
# R2-2: frontmatter の限定 YAML サブセット parser
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fm(*body):
    return "---\n" + "\n".join(body) + "\n---\n\n# 本文\n\ntags: 本文は読まない\n"


class TestYamlSubsetParser(unittest.TestCase):
    """`parse_frontmatter_yaml_subset` は total で、**対応する構文だけ**を読む（R2-2）。"""

    def _doc(self, *body):
        doc, problems = d.parse_frontmatter_yaml_subset(_fm(*body))
        self.assertEqual(problems, [], f"読めるはず: {body}")
        return doc

    def _unparseable(self, *body):
        doc, problems = d.parse_frontmatter_yaml_subset(_fm(*body))
        self.assertIsNone(doc, f"読めてはいけない: {body}")
        self.assertTrue(problems and problems[0].startswith(d.UNPARSEABLE_PREFIX), problems)
        return problems

    # --- 対応する構文 ---
    def test_flat_mapping_and_types(self):
        # R3-11 で期待を変更: `1.5` のような `<数字>.<数字>` は **文字列のまま**にする。
        # 版番号（`document_revision: 0.1` と `0.10`）を float 化で同一視しないため
        doc = self._doc("a: x", "n: 7", "f: 1.5", "t: true", "f2: False", "z: null", "e:",
                        'q: "0.1"', "day: 2026-09-05", "neg: -3", "sci: 1.5e3")
        self.assertEqual(doc, {"a": "x", "n": 7, "f": "1.5", "t": True, "f2": False, "z": None,
                               "e": None, "q": "0.1", "day": "2026-09-05", "neg": -3, "sci": 1500.0})
        self.assertIsInstance(doc["day"], str, "日付は文字列のまま（PyYAML の date 化を再現しない）")
        self.assertIsInstance(doc["q"], str)
        self.assertIsInstance(doc["f"], str, "0.1 と 0.10 を別の版として扱えるようにする（R3-11）")

    def test_nested_block_mapping(self):
        doc = self._doc("extensions:", "  revision:", '    document_revision: "0.10"',
                        "    reason: 実物と同じ形", "kind: brownfield")
        self.assertEqual(doc, {"extensions": {"revision": {"document_revision": "0.10",
                                                           "reason": "実物と同じ形"}},
                               "kind": "brownfield"})

    def test_block_sequence_of_mappings(self):
        doc = self._doc("source_refs:", "  - path: wiki/a.md", "    sha256: " + "a" * 64,
                        "    role: wiki-page", "  - path: wiki/b.md", "    sha256: " + "b" * 64,
                        "    role: runtime")
        self.assertEqual([r["path"] for r in doc["source_refs"]], ["wiki/a.md", "wiki/b.md"])
        self.assertEqual(doc["source_refs"][1]["role"], "runtime")

    def test_block_sequence_of_scalars_and_zero_indent_form(self):
        self.assertEqual(self._doc("bundle_scope:", "  - claude/SKILL.md", "  - claude/recon_gap.py"),
                         {"bundle_scope": ["claude/SKILL.md", "claude/recon_gap.py"]})
        self.assertEqual(self._doc("bundle_scope:", "- claude/SKILL.md", "- x", "kind: b"),
                         {"bundle_scope": ["claude/SKILL.md", "x"], "kind": "b"})

    def test_nested_sequence_under_sequence_mapping(self):
        doc = self._doc("hosts:", "  - host: claude", "    trigger:", "      task_id: t",
                        '      schedule: "0 10 23 * *"', "    bundle:", "      - a", "      - b")
        self.assertEqual(doc["hosts"], [{"host": "claude",
                                         "trigger": {"task_id": "t", "schedule": "0 10 23 * *"},
                                         "bundle": ["a", "b"]}])

    def test_quoted_scalars(self):
        doc = self._doc(r'dq: "a\"b\\c\nd"', "sq: 'it''s'", "plain: a: b は key 優先")
        self.assertEqual(doc["dq"], 'a"b\\c\nd')
        self.assertEqual(doc["sq"], "it's")
        self.assertEqual(doc["plain"], "a: b は key 優先", "値の中の `:` は最初の `:` より後ろ")

    def test_sequence_item_with_colon_inside_quotes_is_a_scalar(self):
        """`- "未着: blocked"` を mapping に読み違えない（黙って構造を作り替えない）。"""
        doc = self._doc("failure_handling:", '  - "CSV が1本でも未着: blocked(input_missing)"',
                        '  - "到着済み不正: precondition_failed"')
        self.assertEqual(doc["failure_handling"],
                         ["CSV が1本でも未着: blocked(input_missing)",
                          "到着済み不正: precondition_failed"])

    def test_comments_and_blank_lines(self):
        doc = self._doc("# 先頭のコメント", "", "a: 1   # 末尾のコメント", 'b: "x # ここはコメントではない"',
                        "c: a#b")
        self.assertEqual(doc, {"a": 1, "b": "x # ここはコメントではない", "c": "a#b"})

    def test_flow_forms(self):
        self.assertEqual(self._doc("tags: [a, b]")["tags"], ["a", "b"])
        self.assertEqual(self._doc("tags: []")["tags"], [])
        self.assertEqual(self._doc("m: {k: v, n: 2}")["m"], {"k": "v", "n": 2})
        self.assertEqual(self._doc("m: {}")["m"], {})

    def test_one_line_json_flow_still_reads(self):
        """R1 が規定した「1行 JSON flow」は YAML flow の部分集合として通り続ける。"""
        refs = [{"path": "wiki/a.md", "sha256": "a" * 64, "role": "wiki-page"}]
        doc = self._doc("source_refs: " + json.dumps(refs, ensure_ascii=False),
                        'effect_contract: {"path": "distill/x/effect-contract.json", '
                        '"sha256": "' + "b" * 64 + '"}')
        self.assertEqual(doc["source_refs"], refs)
        self.assertEqual(doc["effect_contract"]["path"], "distill/x/effect-contract.json")

    def test_body_after_closing_delimiter_is_not_read(self):
        self.assertNotIn("tags", self._doc("a: 1"))

    def test_second_document_delimiter_ends_the_frontmatter(self):
        """複数ドキュメントは parser まで届かない: Core の frontmatter 契約が closing `---` で切る。"""
        doc, problems = d.parse_frontmatter_yaml_subset("---\na: 1\n---\nb: 2\n---\n")
        self.assertEqual((doc, problems), ({"a": 1}, []))

    def test_nested_flow_reads_only_as_one_line_json(self):
        """入れ子 flow は **JSON として妥当なときだけ**読む（R1 の1行 JSON 互換のため）。"""
        self.assertEqual(self._doc("a: [[1, 2], 3]")["a"], [[1, 2], 3])
        self._unparseable("a: [[x, y], z]")

    # --- 対応しない構文（unparseable。黙って部分的に読まない） ---
    UNSUPPORTED = {
        "anchor": ("a: &anchor x", "b: 1"),
        "alias": ("a: x", "b: *anchor"),
        "tag": ("a: !!str 1",),
        "seq_alias": ("a:", "  - *anchor"),
        "block_scalar_literal": ("a: |", "  line1", "  line2"),
        "block_scalar_folded": ("a: >", "  line1"),
        "document_end": ("a: 1", "...", "b: 2"),
        "directive": ("%YAML 1.2", "a: 1"),
        "tab_indent": ("a:", "\tb: 1"),
        "multiline_flow": ("a: [1,", "  2]"),
        "unclosed_quote": ('a: "x',),
        "text_after_quote": ('a: "x" y',),
        "bad_indent": ("a: 1", "  b: 2"),
        "duplicate_key": ("a: 1", "a: 2"),
        "mixed_map_and_seq": ("a: 1", "- b"),
        "value_and_nested": ("a: 1", "  b: 2"),
        "not_a_mapping_line": ("a: 1", "just text"),
        "broken_flow_json": ('a: {path": "x", "sha256": "y"}',),
        "nested_flow_not_json": ("a: [[x, y], z]",),
        "flow_with_stray_quote": ('a: [x", y]',),
        "complex_key": ("? a", ": b"),
        "inline_nested_sequence": ("a:", "  - - x",),
    }

    def test_unsupported_syntax_is_unparseable(self):
        for name, body in self.UNSUPPORTED.items():
            with self.subTest(case=name):
                self._unparseable(*body)

    def test_parser_is_total(self):
        """どんな入力でも例外を投げない（total）。"""
        junk = ["", "---", "---\n---\n", "---\na\n---\n", "---\n:\n---\n", "---\n \t\n---\n",
                "---\n- 1\n---\n", "---\na:\n  - \n---\n", "---\n" + "a: [" * 50 + "\n---\n",
                "---\n" + "\n".join(f"{' ' * i}k{i}:" for i in range(60)) + "\n---\n",
                None, 7, b"---\na: 1\n---\n"]
        for i, text in enumerate(junk):
            with self.subTest(case=i):
                doc, problems = d.parse_frontmatter_yaml_subset(text)
                self.assertTrue(doc is None or isinstance(doc, dict))
                if doc is None:
                    self.assertTrue(problems)

    def test_all_digit_sha256_stays_string(self):
        """R3-7 で**期待を反転**: 64桁すべて数字の sha256 も文字列のままにする。

        以前は int になり、桁を復元できないのに refs が unverifiable（＝FAIL しない）へ静かに
        退避していた。いまは 64桁の数字列と先頭 0 の数字列を文字列として残す。
        """
        doc = self._doc("sha256: " + "1" * 64, 'quoted: "' + "1" * 64 + '"',
                        "zero: 0" + "1" * 63, "n: 7")
        self.assertIsInstance(doc["sha256"], str)
        self.assertIsInstance(doc["quoted"], str)
        self.assertEqual(doc["zero"], "0" + "1" * 63, "先頭 0 を落とさない")
        self.assertIsInstance(doc["n"], int, "普通の整数は int のまま")

    def test_real_vault_shaped_fixture_parses(self):
        """実物の共有 Vault の proposal frontmatter を複製した fixture が**そのまま**読めること。"""
        doc, problems = d.parse_frontmatter_yaml_subset(
            (FIXTURES / "proposal-block-yaml.md").read_text(encoding="utf-8"))
        self.assertEqual(problems, [])
        self.assertEqual(doc["skill_slug"], SLUG)
        self.assertEqual(doc["distill_id"], "d-6ddce7f6")
        self.assertEqual(len(doc["source_refs"]), 6)
        for ref in doc["source_refs"]:
            self.assertEqual(sorted(ref), ["excerpt", "path", "role", "sha256"])
            self.assertRegex(ref["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(doc["effect_contract"]["path"], f"distill/{SLUG}/effect-contract.json")
        self.assertEqual(doc["extensions"]["revision"]["document_revision"], "0.10")
        self.assertEqual(doc["hosts"][0]["trigger"]["task_id"], SLUG)
        self.assertEqual(doc["hosts"][0]["trigger"]["status"], "snapshot")
        self.assertEqual(doc["bundle_scope"], ["claude/SKILL.md", "claude/recon_gap.py"])
        self.assertEqual(doc["sensitive_inputs"][0]["sensitivity"], "high")
        self.assertEqual(doc["review"]["distill_reviewed_at"], "2026-09-05")
        self.assertIsInstance(doc["review"]["distill_reviewed_at"], str)
        self.assertEqual(sorted(doc["extracted_requirements"]),
                         ["failure_handling", "inputs", "outputs", "preconditions", "procedure"])
        self.assertEqual(len(doc["extracted_requirements"]["procedure"]), 7)


def _block_proposal(did, slug, source_refs, ec_ref):
    """実物と同じ**ブロック形式**の proposal frontmatter を組み立てる（1行 JSON を使わない）。"""
    lines = ["---", 'proposal_version: "0.1"',
             "extensions:", "  revision:", '    document_revision: "0.10"',
             '    supersedes: "0.9 (2026-09-07)"',
             '    reason: "R2-2: 人が書くブロック形式"',
             f"skill_slug: {slug}", f"distill_id: {did}", "kind: brownfield", "source_refs:"]
    for ref in source_refs:
        lines.append(f"  - path: {ref['path']}")
        lines.append(f"    sha256: {ref['sha256']}")
        lines.append(f"    role: {ref['role']}")
        lines.append(r'    excerpt: "US フィルタ（\"US\"）が要る # 理由つき"')
    lines += ["extracted_requirements:", "  procedure:",
              '    - "expected account set を run 開始時に固定する"',
              '    - "CSV が1本でも未着: blocked(input_missing)"',
              "effect_contract:", f"  path: {ec_ref['path']}", f"  sha256: {ec_ref['sha256']}",
              "hosts:", "  - host: claude", "    entry: scheduled-task-skill",
              "    trigger:", f"      task_id: {slug}", '      schedule: "0 10 23 * *"',
              "      timezone: Asia/Tokyo", "      status: snapshot",
              "bundle_scope:", "  - claude/SKILL.md", "  - claude/recon_gap.py",
              "sensitive_inputs:", "  - name: Seller Hub アクティブ出品CSV",
              "    sensitivity: high", "    retention: completed なら run 内で削除",
              "review:", "  distill_reviewed_by: とんすけ", '  distill_reviewed_at: "2026-09-05"',
              "tags: [distill, pilot]", "---", "", "# proposal", ""]
    return "\n".join(lines) + "\n"


class TestBlockYamlProposal(DistillCase):
    """R2-2: ブロック形式で書かれた実物の proposal で C（refs 照合）が効くこと。"""

    def setUp(self):
        super().setUp()
        self.outside = Path(self.tmp.name) / "ebay"
        (self.outside / "OpenLister").mkdir(parents=True)
        self.runtime = self.outside / "OpenLister" / "runner.py"
        self.runtime.write_text("print(1)\n", encoding="utf-8")

    def _write(self, did, source_refs=None, ec_sha=None):
        ec = self._write_contract()
        ec_ref = {"path": f"distill/{SLUG}/effect-contract.json",
                  "sha256": ec_sha or d.sha256_file(ec)}
        refs = source_refs if source_refs is not None else [
            {"path": self.page_rel, "sha256": d.sha256_file(self.page), "role": "wiki-page"}]
        path = self.root / "distill" / SLUG / "proposal.md"
        path.write_text(_block_proposal(did, SLUG, refs, ec_ref), encoding="utf-8")
        return path, ec

    def _out(self, **kw):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = d.cmd_validate(self.root, refs=True, **kw)
        return rc, buf.getvalue()

    def test_block_proposal_is_read_and_binds_accepted(self):
        did = self._nominate()
        prop, ec = self._write(did)
        records, problems = d.scan_proposals(self.root)
        self.assertEqual(problems, [])
        self.assertFalse(records[did]["unparseable"])
        self.assertEqual(d.cmd_decide(self.root, did, "accepted", "ok", actor="t"), 0)
        bound = self._last("decision")["bound_to"]
        self.assertEqual(bound["proposal"]["sha256"], d.sha256_file(prop))
        self.assertEqual(bound["effect_contract"]["sha256"], d.sha256_file(ec))
        self.assertEqual(d.cmd_validate(self.root), 0)

    def test_ok_mismatch_unverifiable_from_block_yaml(self):
        did = self._nominate()
        self._write(did, source_refs=[
            {"path": self.page_rel, "sha256": d.sha256_file(self.page), "role": "wiki-page"},
            {"path": "eBay/OpenLister/runner.py", "sha256": d.sha256_file(self.runtime),
             "role": "runtime"},
            {"path": "scheduled-tasks/x/SKILL.md", "sha256": "c" * 64, "role": "existing-skill"},
            {"path": "wiki/concepts/MonthlyListingReconCsv.md", "sha256": "a" * 64, "role": "other"},
        ])
        d.cmd_decide(self.root, did, "accepted", "ok", actor="t")
        rc, out = self._out(ref_bases={"eBay": str(self.outside)})
        self.assertEqual(rc, 2, "mismatch があるので FAIL")
        # effect-contract + wiki-page + runtime = ok 3 / other = mismatch 1 / existing-skill = unverifiable 1
        self.assertIn("refs: ok=3 mismatch=1 unverifiable=1", out)
        self.assertIn("role=runtime", out)
        self.assertNotIn(d.UNPARSEABLE_PREFIX, out)

    def test_all_ok_from_block_yaml(self):
        did = self._nominate()
        self._write(did, source_refs=[
            {"path": self.page_rel, "sha256": d.sha256_file(self.page), "role": "wiki-page"},
            {"path": "eBay/OpenLister/runner.py", "sha256": d.sha256_file(self.runtime),
             "role": "runtime"}])
        d.cmd_decide(self.root, did, "accepted", "ok", actor="t")
        rc, out = self._out(ref_bases={"eBay": str(self.outside)})
        self.assertEqual(rc, 0)
        self.assertIn("refs: ok=3 mismatch=0 unverifiable=0", out)

    def test_effect_contract_ref_mismatch_from_block_yaml(self):
        did = self._nominate()
        self._write(did, ec_sha="d" * 64)
        d.cmd_decide(self.root, did, "accepted", "ok", actor="t")
        rc, out = self._out()
        self.assertEqual(rc, 2)
        self.assertIn("role=effect-contract", out)

    def test_unparseable_proposal_makes_refs_unverifiable(self):
        """対応しない構文が1つでもあれば、refs は unverifiable（部分的に読んだ結果で ok と言わない）。"""
        for name, broken in (("block_scalar", '    excerpt: |'),
                             ("anchor", "  - path: &a wiki/x.md"),
                             ("tab", "\tbad: 1")):
            with self.subTest(case=name):
                self.tearDown()
                self.setUp()
                did = self._nominate()
                prop, _ = self._write(did)
                text = prop.read_text(encoding="utf-8").replace("source_refs:",
                                                                "source_refs:\n" + broken, 1)
                prop.write_text(text, encoding="utf-8")
                records, problems = d.scan_proposals(self.root)
                self.assertTrue(any(d.UNPARSEABLE_PREFIX in x for x in problems), problems)
                self.assertTrue(records[did]["unparseable"])
                rc, out = self._out()
                self.assertEqual(rc, 2, "読めない frontmatter は FAIL（fail-closed）")
                self.assertIn("unverifiable=1", out)
                self.assertIn("ok=0 mismatch=0", out)

    def test_block_and_json_flow_agree(self):
        """同じ内容をブロック形式と1行 JSON で書いたら、読んだ結果が一致すること。"""
        did = self._nominate()
        refs = [{"path": self.page_rel, "sha256": d.sha256_file(self.page), "role": "wiki-page"}]
        self._write(did, source_refs=refs)
        block = d.scan_proposals(self.root)[0][did]["fm"]
        self._write_proposal(did, source_refs=refs)          # R1 の1行 JSON 形式
        flow = d.scan_proposals(self.root)[0][did]["fm"]
        stripped = [{k: v for k, v in r.items() if k != "excerpt"} for r in block["source_refs"]]
        self.assertEqual(stripped, flow["source_refs"])
        self.assertEqual(block["effect_contract"], flow["effect_contract"])


class TestLegacyAcceptedWithoutBoundTo(DistillCase):
    """R2-1: merge 3 より前に書かれた accepted（`bound_to` 無し）を後から不正にしない。"""

    def _backdate_store(self, base="2026-09-08T00:13:", drop_bound_to=None):
        """store の全 event を merge 3 以前へ動かす（event_id の時刻 prefix と連鎖も張り直す）。

        R4-2 で validate が chain の時刻単調性と `event_id` の時刻 prefix を検査するので、head の
        occurred_at だけ戻すと「当時の event」ではなく壊れた chain になる。fixture の時刻を直す
        （検査は緩めない）。`drop_bound_to` の event からは `bound_to` を落とす。
        """
        edir = d.events_dir(self.root)
        pending = sorted((json.loads(p.read_text(encoding="utf-8")) for p in edir.glob("*.json")),
                         key=lambda e: (e["occurred_at"], e["event_id"]))
        for p in edir.glob("*.json"):
            p.unlink()
        # 同一秒の event は file 名（乱数）順になり得るので、連鎖の順に並べ直してから振り直す
        events, done = [], set()
        while pending:
            nxt = [e for e in pending if not e.get("previous_event_id") or e["previous_event_id"] in done]
            self.assertTrue(nxt, "連鎖に繋がらない event がある")
            events.append(nxt[0])
            done.add(nxt[0]["event_id"])
            pending.remove(nxt[0])
        remap, sha, head = {}, {}, None
        for i, ev in enumerate(events):
            at = f"{base}{i:02d}Z"
            new_id = at.replace("-", "").replace(":", "") + ev["event_id"][16:]
            remap[ev["event_id"]] = new_id
            if ev["event_id"] == drop_bound_to:
                ev.pop("bound_to", None)
                head = new_id
            ev["occurred_at"], ev["event_id"] = at, new_id
            if ev.get("previous_event_id"):
                ev["previous_event_id"] = remap[ev["previous_event_id"]]
                ev["previous_event_sha256"] = sha[ev["previous_event_id"]]
            raw = json.dumps(ev, ensure_ascii=False, sort_keys=True)
            (edir / f"{new_id}.json").write_text(raw, encoding="utf-8")
            sha[new_id] = d.sha256_bytes(raw.encode("utf-8"))
        return head

    def _make_legacy(self):
        """accepted を書いてから head event の `bound_to` を落とす（当時の event を再現する）。

        R3-9: legacy と認めるのは `MERGE3_CUTOFF` **より前**の event だけになったので、
        occurred_at も merge 3 以前に戻す（そうでないと「当時の event」の再現になっていない）。
        R4-2: 時刻は chain 全体（`event_id` の prefix 込み）で戻す。
        """
        did = self._nominate()
        self._write_proposal(did)
        self.assertEqual(d.cmd_decide(self.root, did, "accepted", "ok", actor="t"), 0)
        head_id = self._backdate_store(drop_bound_to=self._last("decision")["event_id"])
        d.cmd_reindex(self.root)      # 派生 index も当時の内容に合わせる（occurred_at を戻したので）
        # R5 (G15): legacy は occurred_at の窓ではなく **既知 event_id の固定リスト**で判定する
        # （時刻は書き手が決められる値なので、窓では backdate した accepted を legacy に化けさせられた）。
        # このテストの「当時の event」を、テスト内でだけリストに登録する
        # R5 (G19): id だけでなく file bytes の sha256 も登録する（名前だけの偽造を認めない）
        import hashlib as _hl
        head_sha = _hl.sha256((self.root / "distill" / "events" / f"{head_id}.json").read_bytes()).hexdigest()
        original = dict(d.LEGACY_ACCEPTED_EVENTS)
        d.LEGACY_ACCEPTED_EVENTS[head_id] = head_sha
        self.addCleanup(lambda: (d.LEGACY_ACCEPTED_EVENTS.clear(), d.LEGACY_ACCEPTED_EVENTS.update(original)))
        return did, head_id

    def _out(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = d.cmd_validate(self.root)
        return rc, buf.getvalue()

    def test_legacy_accepted_is_reported_not_failed(self):
        did, eid = self._make_legacy()
        events = d.load_events(self.root)
        self.assertTrue(any(e["event_id"] == eid for e in events), "legacy event が捨てられていない")
        rc, out = self._out()
        self.assertEqual(rc, 0, "legacy は FAIL にしない")
        self.assertIn("bound_to legacy", out)
        self.assertIn("legacy=1", out)
        self.assertIn(did, out)
        problems, legacy = d.check_bound_drift(self.root, events, d.candidate_states(events))
        self.assertEqual(problems, [])
        self.assertEqual(len(legacy), 1)

    def test_legacy_accepted_skips_drift_check(self):
        """束縛が無いので drift は検査しない（「検査していない」を「一致」と混ぜないので legacy に出す）。"""
        did, _ = self._make_legacy()
        (self.root / "distill" / SLUG / "proposal.md").write_text("---\nx: 1\n---\n", encoding="utf-8")
        rc, out = self._out()
        self.assertEqual(rc, 2, "proposal の frontmatter が壊れたことは別途 FAIL")
        self.assertNotIn("proposal drift", out)
        self.assertIn("legacy=1", out)

    def test_rereview_clears_legacy(self):
        did, _ = self._make_legacy()
        self.assertEqual(d.cmd_rereview(self.root, did, "改訂版を再レビューした", actor="tonsuke"), 0)
        ev = self._last("rereviewed")
        self.assertEqual(ev["new_state"], "accepted")
        self.assertIn("bound_to", ev)
        rc, out = self._out()
        self.assertEqual(rc, 0)
        self.assertNotIn("bound_to legacy", out)
        self.assertIn("legacy=0", out)

    def test_cli_always_writes_bound_to(self):
        """新しい CLI が hash 無しの accepted を書く経路は無い（legacy は過去の event だけ）。"""
        did = self._nominate()
        self._write_proposal(did)
        d.cmd_decide(self.root, did, "accepted", "ok", actor="t")
        self.assertIn("bound_to", self._last("decision"))
        # proposal が無い候補では accepted は**書かれない**（hash 無しの accepted を残さない）
        page2 = self.root / "wiki" / "concepts" / "Other.md"
        page2.write_text(PAGE, encoding="utf-8")
        d.cmd_nominate(self.root, "wiki/concepts/Other.md", "2件目", actor="t")
        did2 = d.read_frontmatter(page2)["distill_id"]
        before = sorted(p.name for p in d.events_dir(self.root).glob("*.json"))
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did2, "accepted", "x", actor="t")
        self.assertEqual(sorted(p.name for p in d.events_dir(self.root).glob("*.json")), before)

class TestMerge3R3(DistillCase):
    """R3（攻撃側レビューへの修正）の回帰。probe（tests/redteam_merge3_probes.py）とは別に kit 側で縛る。"""

    def _forge_decision(self, did, **over):
        """CLI を迂回して schema 的に正しい decision(accepted) を書く（write_event は検証を通す）。"""
        head = d.state_head(self._events(), did)[1]
        ev = d._base_event("decision", dict(head["subject"]), "human", "forger", reason="forged")
        ev.update(expected_previous_state="nominated", new_state="accepted",
                  previous_event_id=head["event_id"], previous_event_sha256=head["_sha256"])
        ev.update(over)
        return d.write_event(self.root, ev)

    # --- R3-1 / R3-2: 束縛先は自分の proposal / effect contract -------------------
    def test_bound_to_must_point_at_own_proposal(self):
        did = self._nominate()
        prop, ec = self._write_proposal(did)
        notes = self.root / "distill" / SLUG / "notes.md"
        notes.write_text("not a proposal\n", encoding="utf-8")
        self._forge_decision(did, bound_to={
            "proposal": {"path": f"distill/{SLUG}/notes.md", "sha256": d.sha256_file(notes)},
            "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json",
                                "sha256": d.sha256_file(ec)}})
        d.cmd_reindex(self.root)
        self.assertEqual(d.cmd_validate(self.root), 2, "hash が合っていても束縛先が違えば FAIL")

    def test_bound_to_effect_contract_may_not_point_elsewhere(self):
        did = self._nominate()
        prop, _ = self._write_proposal(did)
        self._forge_decision(did, bound_to={
            "proposal": {"path": f"distill/{SLUG}/proposal.md", "sha256": d.sha256_file(prop)},
            "effect_contract": {"path": self.page_rel, "sha256": d.sha256_file(self.page)}})
        d.cmd_reindex(self.root)
        self.assertEqual(d.cmd_validate(self.root), 2)

    def test_path_alias_does_not_satisfy_owner_check(self):
        """大小文字・末尾ドットの alias は、解決先が同じ file でも「同じ path」と認めない。"""
        canonical = f"distill/{SLUG}/proposal.md"
        # 末尾ドットは Windows では同じ file に解決するが、正規化した文字列としては別物
        self.assertNotEqual(d.normalize_portable(canonical + "."), d.normalize_portable(canonical))
        # 大小文字だけが違う**別 candidate**の path も、畳み込んだ上で別物と分かる
        self.assertNotEqual(d.normalize_portable("Distill/Other-Slug/PROPOSAL.MD"),
                            d.normalize_portable(canonical))
        did = self._nominate()
        _, ec = self._write_proposal(did)
        other = self.root / "distill" / "other-slug"
        other.mkdir(parents=True)
        (other / "proposal.md").write_text("---\nskill_slug: other-slug\n---\n", encoding="utf-8")
        self._forge_decision(did, bound_to={
            "proposal": {"path": "Distill/Other-Slug/PROPOSAL.MD",
                         "sha256": d.sha256_file(other / "proposal.md")},
            "effect_contract": {"path": f"distill/{SLUG}/effect-contract.json",
                                "sha256": d.sha256_file(ec)}})
        d.cmd_reindex(self.root)
        self.assertEqual(d.cmd_validate(self.root), 2, "alias 経由の束縛は owner 検査を通らない")

    def test_portable_path_rejects_leading_and_trailing_space_segments(self):
        for bad in ("wiki/a /b.md", "wiki/ a.md", "wiki/a.md "):
            self.assertIsNone(d.PORTABLE_PATH_RE.match(bad), bad)
        self.assertIsNotNone(d.PORTABLE_PATH_RE.match("wiki/a b.md"), "途中の空白は許す")

    # --- R3-3: candidate identity（page_path）の連続性 ---------------------------
    def test_nominate_rejects_distill_id_owned_by_another_page(self):
        did = self._nominate()
        d.cmd_decide(self.root, did, "held", "later", actor="t")
        other_rel = "wiki/concepts/Other.md"
        (self.root / other_rel).write_text(self.page.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(d.DistillError):
            d.cmd_nominate(self.root, other_rel, "hijack", actor="t")
        self.assertEqual(d.state_head(self._events(), did)[1]["subject"]["page_path"], self.page_rel)

    def test_state_chain_rejects_page_path_change(self):
        did = self._nominate()
        self._write_proposal(did)
        other_rel = "wiki/concepts/Other.md"
        (self.root / other_rel).write_text("---\ntitle: other\n---\nno review\n", encoding="utf-8")
        self._forge_decision(did, bound_to=d.compute_bound_to(self.root, did), subject={
            "subject_type": "page", "distill_id": did, "page_path": other_rel,
            "page_sha256": d.sha256_file(self.root / other_rel)})
        with self.assertRaises(d.DistillError):
            d.state_chain(self._events(), did)
        self.assertEqual(d.cmd_validate(self.root, strict_index=False), 2)

    # --- R3-4: index が head event の sha を持つ --------------------------------
    def test_index_records_head_event_sha(self):
        did = self._nominate()
        head = d.state_head(self._events(), did)[1]
        idx = (self.root / "distill" / "_index.md").read_text(encoding="utf-8")
        self.assertIn(head["_sha256"], idx)
        # head を書き換えると、reindex を忘れている限り index との不一致で拾える
        p = d.events_dir(self.root) / f"{head['event_id']}.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["reason"] = "書き換えた"
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        self.assertEqual(d.cmd_validate(self.root), 2)

    # --- R3-9: legacy は cutoff より前の event だけ ------------------------------
    def test_new_accepted_without_bound_to_fails(self):
        did = self._nominate()
        self._write_proposal(did)
        self._forge_decision(did)                      # bound_to 無し・occurred_at は現在
        d.cmd_reindex(self.root)
        self.assertEqual(d.cmd_validate(self.root), 2, "cutoff 以降の束縛無し accepted は legacy ではない")

    # --- R3-10: rereview は bundle 束縛を黙って外さない --------------------------
    def test_rereview_keeps_and_can_drop_candidate_bundle(self):
        did = self._nominate()
        self._accept(did, bundle_sha256="c" * 64)
        head = lambda: d.state_head(self._events(), did)[1]      # noqa: E731（同一秒でも head を取る）
        self.assertEqual(d.cmd_rereview(self.root, did, "再レビュー", actor="t"), 0)
        self.assertEqual(head()["bound_to"]["candidate_bundle"]["sha256"], "c" * 64)
        self.assertEqual(d.cmd_rereview(self.root, did, "束縛を外す", actor="t", drop_bundle=True), 0)
        self.assertNotIn("candidate_bundle", head()["bound_to"])

    # --- R3-5: unparseable な proposal は束縛しない ------------------------------
    def test_unparseable_proposal_blocks_accept(self):
        did = self._nominate()
        self._write_contract(SLUG)
        (self.root / "distill" / SLUG / "proposal.md").write_text(
            f'---\nskill_slug: {SLUG}\ndistill_id: {did}\nnote: |\n  block\n---\n', encoding="utf-8")
        before = sorted(p.name for p in d.events_dir(self.root).glob("*.json"))
        with self.assertRaises(d.DistillError):
            d.cmd_decide(self.root, did, "accepted", "r", actor="t")
        self.assertEqual(sorted(p.name for p in d.events_dir(self.root).glob("*.json")), before)

    # --- R3-6 / R3-12 / R3-14 / R3-16 -------------------------------------------
    def test_apostrophe_in_plain_scalar_is_read(self):
        doc, problems = d.parse_frontmatter_yaml_subset(
            "---\nnote: it's fine\nq: \"（既定dry-run）。--apply は\" # c\nd: don't # x\n---\n")
        self.assertEqual(problems, [])
        self.assertEqual(doc, {"note": "it's fine", "q": "（既定dry-run）。--apply は", "d": "don't"})

    def test_json_flow_duplicate_key_nan_and_depth(self):
        for bad in ('x: {"a": 1, "a": 2}', "x: [NaN, Infinity]",
                    "x: " + "[" * 9 + "1" + "]" * 9):
            with self.subTest(bad=bad):
                doc, problems = d.parse_frontmatter_yaml_subset("---\n" + bad + "\n---\n")
                self.assertIsNone(doc, bad)
                self.assertTrue(problems[0].startswith(d.UNPARSEABLE_PREFIX), problems)

    def test_split_key_is_linear(self):
        import time
        line = "a" + " " * 200000 + "b: c"
        start = time.time()
        d._yaml_split_key(line)
        self.assertLess(time.time() - start, 1.0, "1行の長さに対して線形であること")

    def test_safe_escapes_bidi_and_line_separators(self):
        s = d._safe("a‮b c⁩de")
        for ch in ("‮", " ", "⁩", ""):
            self.assertNotIn(ch, s)
        self.assertIn("\\u202e", s)

    def test_index_escapes_bidi_in_page_path(self):
        rel = "wiki/concepts/ab‮.md"
        (self.root / rel).write_text(PAGE, encoding="utf-8")
        d.cmd_nominate(self.root, rel, "bidi", actor="t")
        self.assertNotIn("‮", (self.root / "distill" / "_index.md").read_text(encoding="utf-8"))
        self.assertEqual(d.cmd_validate(self.root), 0)

    # --- R3-7 / R3-15 ------------------------------------------------------------
    def test_numeric_slug_directory_is_kept(self):
        did = self._nominate()
        slug = "12345678"
        self._write_contract(slug)
        (self.root / "distill" / slug / "proposal.md").write_text(
            f"---\nskill_slug: {slug}\ndistill_id: {did}\nkind: brownfield\n---\n", encoding="utf-8")
        records, problems = d.scan_proposals(self.root)
        self.assertIn(did, records, problems)

    def test_numeric_sha256_ref_is_mismatch_not_unverifiable(self):
        did = self._nominate()
        self._write_proposal(did, source_refs=[{"path": self.page_rel, "sha256": 1234, "role": "x"}])
        records, _ = d.scan_proposals(self.root)
        _, counts, _ = d.check_refs(self.root, records, {})
        self.assertEqual(counts["mismatch"], 1, counts)


if __name__ == "__main__":
    unittest.main()
