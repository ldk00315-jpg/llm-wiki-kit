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
        d.cmd_decide(self.root, did, "accepted", "ok", actor="t")
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
        self.assertEqual(d.cmd_decide(self.root, did, "accepted", "ok", actor="t"), 0)


if __name__ == "__main__":
    unittest.main()
