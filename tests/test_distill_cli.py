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
        self.assertEqual(d.cmd_decide(self.root, did, "accepted", "sandbox 証拠を確認", actor="tonsuke"), 0)
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
        d.cmd_decide(self.root, did, "accepted", "ok", actor="t")
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
        d.new_event_id = lambda: fixed          # 常に同じ ID を返す（衝突を強制）
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
        d.reindex(self.root)
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
        did = self._nominate()
        d.cmd_note(self.root, "opportunity", distill_id=did, task_id=None, trigger_source="scheduled",
                   trigger_ref="r1", opportunity_id=None, block_kind=None, source="host-task",
                   strength="observed", reason=None, task_metadata=None, unverifiable_reason=None, actor="h")
        oid = self._last("opportunity")["opportunity_id"]
        for kind in ("completed", "blocked"):
            kw = {"block_kind": "input_missing"} if kind == "blocked" else {"block_kind": None}
            d.cmd_note(self.root, kind, distill_id=did, task_id=None, trigger_source="scheduled",
                       trigger_ref=None, opportunity_id=oid, source="host-task", strength="observed",
                       reason=None, task_metadata=None, unverifiable_reason=None, actor="h", **kw)
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


if __name__ == "__main__":
    unittest.main()
