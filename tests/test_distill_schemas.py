# -*- coding: utf-8 -*-
"""distill schema 4枚の型検査と、レビュー（merge 1・M-01〜M-06・R2-01〜R2-03）で提示された negative instance の拒否を固定する。
jsonschema が無い環境では skip（docs/schema-only の merge 1 では実行依存を増やさない）。"""
import json
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator as V
except ImportError:  # pragma: no cover
    V = None

ROOT = Path(__file__).resolve().parent.parent / "schema" / "distill"
SHA = "a" * 64
UTC = "2026-09-04T09:00:00Z"
EID = "20260904T080000Z-0badcafe"
BAD_PATHS = ("C:/x/y.md", "/abs/y.md", "~/y.md", "a\\b.md", "")


def _load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _ok(schema, inst):
    V(schema).validate(inst)


def _bad(schema, inst):
    return len(list(V(schema).iter_errors(inst))) > 0


@unittest.skipIf(V is None, "jsonschema not installed")
class TestSchemasAreValid(unittest.TestCase):
    def test_all_four_schemas_pass_check_schema(self):
        for n in ("distill-event", "effect-contract", "proposal", "manifest"):
            V.check_schema(_load(f"{n}.schema.json"))


@unittest.skipIf(V is None, "jsonschema not installed")
class TestDistillEvent(unittest.TestCase):
    def setUp(self):
        self.s = _load("distill-event.schema.json")
        self.base = {"event_id": "20260904T090000Z-0badcafe", "occurred_at": UTC, "actor": "host",
                     "subject": {"subject_type": "task", "task_id": "monthly-listing-recon-csv"},
                     "source": "host-task", "strength": "observed"}

    def _page(self):
        return {"subject_type": "page", "distill_id": "d-0badcafe", "page_path": "wiki/concepts/X.md", "page_sha256": SHA}

    def _opp(self, **trigger):
        t = {"trigger_source": "scheduled", "trigger_ref": "run-1", "task_metadata_status": "unverifiable",
             "unverifiable_reason": "no adapter"}
        t.update(trigger)
        return dict(self.base, event_type="opportunity", opportunity_id="op-20260904T090000Z-0badcafe", trigger=t)

    # --- M-01 ---
    def test_opportunity_ok_unverifiable_and_snapshot(self):
        _ok(self.s, self._opp())
        snap = self._opp(task_metadata_status="snapshot", task_metadata={"cronExpression": "0 10 23 * *"})
        snap["trigger"].pop("unverifiable_reason")
        _ok(self.s, snap)

    def test_m01_snapshot_without_metadata_rejected(self):
        e = self._opp(task_metadata_status="snapshot")
        e["trigger"].pop("unverifiable_reason")
        self.assertTrue(_bad(self.s, e))

    def test_m01_unverifiable_without_reason_rejected(self):
        e = self._opp()
        e["trigger"].pop("unverifiable_reason")
        self.assertTrue(_bad(self.s, e))

    def test_m01_unverifiable_with_full_snapshot_rejected(self):
        self.assertTrue(_bad(self.s, self._opp(task_metadata={"x": 1})))

    def test_m01_trigger_ref_required_nonempty(self):
        e = self._opp()
        e["trigger"].pop("trigger_ref")
        self.assertTrue(_bad(self.s, e))
        self.assertTrue(_bad(self.s, self._opp(trigger_ref="")))

    def test_blocked_requires_block_kind_and_completed_forbids_it(self):
        b = dict(self.base, event_type="blocked", opportunity_id="op-20260904T090000Z-0badcafe")
        self.assertTrue(_bad(self.s, b))
        _ok(self.s, dict(b, block_kind="input_missing"))
        self.assertTrue(_bad(self.s, dict(b, event_type="completed", block_kind="input_missing")))

    # --- M-02 / R2-02 ---
    def test_m02_registered_is_non_state_event(self):
        r = dict(self.base, event_type="registered", subject=self._page(), source="human", actor="tonsuke", reason="register")
        _ok(self.s, r)
        self.assertTrue(_bad(self.s, dict(r, expected_previous_state="absent", new_state="nominated")))

    def test_r2_02_registered_page_only(self):
        r = dict(self.base, event_type="registered", source="human", actor="tonsuke", reason="register")
        self.assertTrue(_bad(self.s, r))  # subject_type=task
        mixed = dict(r, subject={"subject_type": "task", "task_id": "t", "distill_id": "d-0badcafe", "page_path": "w.md", "page_sha256": SHA})
        self.assertTrue(_bad(self.s, mixed))  # task に page field 混在
        self.assertTrue(_bad(self.s, dict(r, subject={"subject_type": "skill", "skill_slug": "x-y"})))

    def test_m02_observed_is_system_and_needs_threshold(self):
        o = dict(self.base, event_type="observed", subject=self._page(), source="system", actor="wiki-health",
                 expected_previous_state="absent", new_state="observed",
                 threshold={"window_days": 30, "min_opportunities": 3, "counted_event_ids": [EID]})
        _ok(self.s, o)
        self.assertTrue(_bad(self.s, dict(o, source="human")))
        o2 = dict(o); o2.pop("threshold"); self.assertTrue(_bad(self.s, o2))

    def test_m02_nominated_transitions(self):
        n = dict(self.base, event_type="nominated", subject=self._page(), source="human", actor="tonsuke", reason="x",
                 expected_previous_state="absent", new_state="nominated")
        _ok(self.s, n)
        self.assertTrue(_bad(self.s, dict(n, expected_previous_state="observed")))  # previous 束縛なし
        _ok(self.s, dict(n, expected_previous_state="observed", previous_event_id=EID, previous_event_sha256=SHA))
        self.assertTrue(_bad(self.s, dict(n, new_state="accepted")))

    def test_r2_02_nominated_absent_forbids_previous_and_dangling_rejected(self):
        n = dict(self.base, event_type="nominated", subject=self._page(), source="human", actor="tonsuke", reason="x",
                 expected_previous_state="absent", new_state="nominated")
        self.assertTrue(_bad(self.s, dict(n, previous_event_id=EID)))  # absent なのに previous
        self.assertTrue(_bad(self.s, dict(n, previous_event_id=EID, previous_event_sha256=SHA)))
        self.assertTrue(_bad(self.s, dict(n, expected_previous_state="held", previous_event_id=EID)))  # 片側だけ
        self.assertTrue(_bad(self.s, dict(n, expected_previous_state="held", previous_event_sha256=SHA)))

    def test_m02_decision_always_binds_previous(self):
        d = dict(self.base, event_type="decision", subject=self._page(), source="human", actor="tonsuke", reason="ok",
                 expected_previous_state="nominated", new_state="accepted")
        self.assertTrue(_bad(self.s, d))
        _ok(self.s, dict(d, previous_event_id=EID, previous_event_sha256=SHA))

    def test_r2_02_system_not_allowed_for_opportunity_family(self):
        self.assertTrue(_bad(self.s, dict(self._opp(), source="system")))
        for t in ("invoked", "completed"):
            e = dict(self.base, event_type=t, opportunity_id="op-20260904T090000Z-0badcafe")
            _ok(self.s, e)
            self.assertTrue(_bad(self.s, dict(e, source="system")))
        b = dict(self.base, event_type="blocked", opportunity_id="op-20260904T090000Z-0badcafe", block_kind="input_missing", source="system")
        self.assertTrue(_bad(self.s, b))

    def test_r2_02_irrelevant_fields_rejected_per_type(self):
        opp = self._opp()
        self.assertTrue(_bad(self.s, dict(opp, new_state="nominated", expected_previous_state="absent")))
        self.assertTrue(_bad(self.s, dict(opp, threshold={"window_days": 30, "min_opportunities": 3, "counted_event_ids": [EID]})))
        inv = dict(self.base, event_type="invoked", opportunity_id="op-20260904T090000Z-0badcafe")
        self.assertTrue(_bad(self.s, dict(inv, trigger=opp["trigger"])))
        dec = dict(self.base, event_type="decision", subject=self._page(), source="human", actor="t", reason="r",
                   expected_previous_state="nominated", new_state="held", previous_event_id=EID, previous_event_sha256=SHA)
        self.assertTrue(_bad(self.s, dict(dec, opportunity_id="op-20260904T090000Z-0badcafe")))

    # --- R2-03 ---
    def test_r2_03_portable_paths_in_event(self):
        n = dict(self.base, event_type="nominated", subject=self._page(), source="human", actor="t", reason="x",
                 expected_previous_state="absent", new_state="nominated")
        for bad in BAD_PATHS:
            e = json.loads(json.dumps(n)); e["subject"]["page_path"] = bad
            self.assertTrue(_bad(self.s, e), bad)
            e = json.loads(json.dumps(n)); e["evidence"] = [{"path": bad, "sha256": SHA}]
            self.assertTrue(_bad(self.s, e), bad)
        _ok(self.s, dict(n, evidence=[{"path": "distill/x/evidence.json", "sha256": SHA}]))


@unittest.skipIf(V is None, "jsonschema not installed")
class TestManifest(unittest.TestCase):
    def setUp(self):
        self.s = _load("manifest.schema.json")
        self.base = {"skill": "monthly-listing-recon-csv", "version": 1, "state": "proposed", "created": UTC, "supersedes": None,
                     "supersede_reason": "initial", "runtime_refs": [], "common_runtime_sha256": SHA,
                     "candidate_bundles": {"logical_paths": ["claude/SKILL.md"], "claude": SHA}, "deployed_bundles": None,
                     "decision": {"status": None}, "validation_evidence": [],
                     "wiki_refs": [{"base_id": "vault", "path": "wiki/concepts/X.md", "sha256": SHA, "review": "approved:2026-09-04"}],
                     "effect_contract": {"path": "distill/x/effect-contract.json", "sha256": SHA},
                     "observation": {"min_observation_period_days": 28, "min_eligible_opportunities": 3, "min_completed_opportunities": 2, "verdict": "pending"}}

    def _bound(self):
        return {"manifest_version": 1, "candidate_bundle_sha256": SHA, "common_runtime_sha256": SHA, "effect_contract_sha256": SHA, "wiki_ref_sha256s": [SHA]}

    def _approved(self):
        return {"status": "approved", "by": "t", "at": UTC, "reason": "r", "given_via": "chat", "bound_to": self._bound()}

    def _dep(self, logical="claude/SKILL.md", deployed="scheduled-tasks/x/SKILL.md"):
        return {"claude": {"deployment_id": "x", "deployed_at": UTC, "files": [{"logical_path": logical, "deployed_path": deployed, "sha256": SHA}]}}

    def test_proposed_ok(self):
        _ok(self.s, self.base)

    def test_m03_approved_without_binding_rejected(self):
        self.assertTrue(_bad(self.s, dict(self.base, decision={"status": "approved"})))
        self.assertTrue(_bad(self.s, dict(self.base, decision={"status": "approved", "by": "t", "at": UTC, "reason": "r", "given_via": "chat"})))
        _ok(self.s, dict(self.base, decision=self._approved()))

    def test_m03_deployed_requires_approved_and_bundles(self):
        m = dict(self.base, state="deployed", deployed_bundles=self._dep())
        self.assertTrue(_bad(self.s, m))
        m["decision"] = self._approved()
        _ok(self.s, m)
        self.assertTrue(_bad(self.s, dict(m, deployed_bundles=None)))
        self.assertTrue(_bad(self.s, dict(self.base, deployed_bundles=self._dep())))

    def test_m03_rejected_requires_reject_decision(self):
        self.assertTrue(_bad(self.s, dict(self.base, state="rejected")))
        _ok(self.s, dict(self.base, state="rejected", decision=dict(self._approved(), status="rejected")))

    def test_m04_zero_thresholds_rejected(self):
        self.assertTrue(_bad(self.s, dict(self.base, observation=dict(self.base["observation"], min_eligible_opportunities=0))))

    def test_m04_pass_requires_audit_fields_and_counts(self):
        o = dict(self.base["observation"], verdict="pass")
        self.assertTrue(_bad(self.s, dict(self.base, observation=o)))
        audit = dict(o, verdict_by="t", verdict_at=UTC, verdict_reason="ok", window={"start": UTC, "end": UTC},
                     eligible_count=3, completed_count=2, blocked_count=1, unterminated_count=0, terminal_conflicts=0,
                     event_set={"head_event_id": EID, "events_sha256": SHA, "evidence": {"path": "distill/x/events-2026-09.json", "sha256": SHA}})
        _ok(self.s, dict(self.base, observation=audit))
        self.assertTrue(_bad(self.s, dict(self.base, observation=dict(audit, eligible_count=0, completed_count=0))))
        self.assertTrue(_bad(self.s, dict(self.base, observation=dict(audit, terminal_conflicts=1))))
        self.assertTrue(_bad(self.s, dict(self.base, observation=dict(audit, unterminated_count=1))))

    def test_r2_03_portable_paths_in_manifest(self):
        for bad in BAD_PATHS:
            m = json.loads(json.dumps(self.base)); m["wiki_refs"][0]["path"] = bad
            self.assertTrue(_bad(self.s, m), bad)
            m = json.loads(json.dumps(self.base)); m["candidate_bundles"]["logical_paths"] = [bad]
            self.assertTrue(_bad(self.s, m), bad)
            m = json.loads(json.dumps(self.base)); m["effect_contract"]["path"] = bad
            self.assertTrue(_bad(self.s, m), bad)
            dep = dict(self.base, state="deployed", decision=self._approved(), deployed_bundles=self._dep(logical=bad))
            self.assertTrue(_bad(self.s, dep), bad)
            dep = dict(self.base, state="deployed", decision=self._approved(), deployed_bundles=self._dep(deployed=bad))
            self.assertTrue(_bad(self.s, dep), bad)
        m = json.loads(json.dumps(self.base)); m["wiki_refs"][0].pop("base_id")
        self.assertTrue(_bad(self.s, m))


@unittest.skipIf(V is None, "jsonschema not installed")
class TestProposal(unittest.TestCase):
    def setUp(self):
        self.s = _load("proposal.schema.json")
        self.base = {"proposal_version": "0.1", "skill_slug": "monthly-listing-recon-csv", "distill_id": "d-0badcafe", "kind": "brownfield",
                     "source_refs": [{"path": "wiki/concepts/MonthlyListingReconCsv.md", "sha256": SHA, "role": "wiki-page"}],
                     "extracted_requirements": {"procedure": ["a"], "inputs": ["csv"], "outputs": ["report"], "failure_handling": ["block"]},
                     "effect_contract": {"path": "distill/monthly-listing-recon-csv/effect-contract.json", "sha256": SHA},
                     "hosts": [{"host": "claude", "entry": "scheduled-task-skill", "canonical_deploy_path": "scheduled-tasks/monthly-listing-recon-csv/SKILL.md"}],
                     "bundle_scope": ["claude/SKILL.md"],
                     "review": {"distill_reviewed_by": "tonsuke", "distill_reviewed_at": "2026-09-04"}}

    def test_ok(self):
        _ok(self.s, self.base)

    def test_r2_03_portable_paths_in_proposal(self):
        for bad in BAD_PATHS:
            p = json.loads(json.dumps(self.base)); p["source_refs"][0]["path"] = bad
            self.assertTrue(_bad(self.s, p), bad)
            p = json.loads(json.dumps(self.base)); p["effect_contract"]["path"] = bad
            self.assertTrue(_bad(self.s, p), bad)
            p = json.loads(json.dumps(self.base)); p["hosts"][0]["canonical_deploy_path"] = bad
            self.assertTrue(_bad(self.s, p), bad)
            p = json.loads(json.dumps(self.base)); p["bundle_scope"] = [bad]
            self.assertTrue(_bad(self.s, p), bad)


@unittest.skipIf(V is None, "jsonschema not installed")
class TestEffectContract(unittest.TestCase):
    """候補(a) fixture（docs/distillation-candidate-a-fixture.md §4）の e01〜e07 を 1 件の contract として固定する。
    対応表: e01 human_action / e02 CSV read / e03 DB read / e04 report create / e05 notify / e06 CSV delete / e07 report delete"""

    def setUp(self):
        self.s = _load("effect-contract.schema.json")
        self.head = {"contract_version": "0.1", "skill_slug": "monthly-listing-recon-csv",
                     "attestation": {"declared_by": "claude", "declared_at": UTC,
                                     "guarantee_ceiling": "review+probe+sandbox: undeclared effects found there fail validation; no runtime deny claimed"},
                     "concurrency": {"lock": "pipeline-wide", "stale_recovery": "manual-only"}}
        self.e01 = {"id": "e01", "resource": "human", "target": "Seller Hub Active Listings CSV", "op": "human_action", "reversibility": "none", "idempotency": "idempotent", "human_action": "required"}
        self.e02 = {"id": "e02", "resource": "local-file", "target": "runs/<run_id>/input/active-listings.csv", "op": "read", "reversibility": "none", "idempotency": "idempotent", "human_action": "none",
                    "local_io": {"kind": "input", "sensitivity": "high", "retention": "deleted by e06", "redaction": "ItemID hashed; title/price/qty not in evidence"}, "freshness": "mtime within current month", "completeness": "rows>0 and required columns"}
        self.e03 = {"id": "e03", "resource": "local-db", "target": "openlister/listings.db", "op": "read", "reversibility": "none", "idempotency": "idempotent", "human_action": "none", "freshness": "fetched_at recorded", "completeness": "active count recorded"}
        self.e04 = {"id": "e04", "resource": "local-file", "target": "runs/<run_id>/report.json", "op": "create", "reversibility": "recreate_from_source", "idempotency": "keyed", "human_action": "none",
                    "local_io": {"kind": "output", "sensitivity": "high", "retention": "90d, deleted by e07", "redaction": "evidence keeps sha256+count only"}, "postcondition": "rows+schema"}
        self.e05 = {"id": "e05", "resource": "notification", "target": "tonsuke", "op": "notify", "reversibility": "none", "idempotency": "keyed", "human_action": "none"}
        self.e06 = {"id": "e06", "resource": "local-file", "target": "runs/<run_id>/input/*.csv", "op": "delete", "reversibility": "none", "idempotency": "idempotent", "human_action": "none",
                    "actor": "skill", "trigger": "next prepare (completed) or blocked>=30d", "precondition": "terminal completed or blocked>=30d and report exists", "postcondition": "path absent + delete event",
                    "local_io": {"kind": "input", "sensitivity": "high", "retention": "n/a (this effect deletes)", "redaction": "n/a"}, "irreversible_ack": True}
        self.e07 = {"id": "e07", "resource": "local-file", "target": "runs/<run_id>/report.json", "op": "delete", "reversibility": "none", "idempotency": "idempotent", "human_action": "none",
                    "actor": "skill", "trigger": "prepare", "precondition": "created+90d", "postcondition": "path absent + delete event",
                    "local_io": {"kind": "output", "sensitivity": "high", "retention": "n/a (this effect deletes)", "redaction": "n/a"}, "irreversible_ack": True}

    def _c(self, *effects):
        return dict(self.head, effects=list(effects))

    def test_r2_01_candidate_a_all_seven_effects_validate(self):
        _ok(self.s, self._c(self.e01, self.e02, self.e03, self.e04, self.e05, self.e06, self.e07))

    def test_r2_01_e07_with_recreate_from_source_and_ack_rejected(self):
        # fixture v1 の矛盾形（reversibility=recreate_from_source ＋ irreversible_ack・backup 無し）
        self.assertTrue(_bad(self.s, self._c(dict(self.e07, reversibility="recreate_from_source"))))

    def test_create_and_write_and_delete_conditions(self):
        self.assertTrue(_bad(self.s, self._c(dict(self.e04, backup={"method": "x", "taken_before_write": True, "scope": "y"}))))  # create に backup
        self.assertTrue(_bad(self.s, self._c(dict(self.e04, op="write"))))  # write に backup 無し
        d = dict(self.e06); d.pop("irreversible_ack"); self.assertTrue(_bad(self.s, self._c(d)))  # delete: ack も backup も無し
        d = dict(self.e06); d.pop("actor"); self.assertTrue(_bad(self.s, self._c(d)))
        with_backup = {k: v for k, v in self.e06.items() if k != "irreversible_ack"}
        with_backup.update(reversibility="backup_restore", backup={"method": "copy", "taken_before_write": True, "scope": "input csv"})
        _ok(self.s, self._c(with_backup))  # delete は backup 経路でも通る

    def test_read_requires_freshness_and_completeness(self):
        r = dict(self.e03); r.pop("completeness")
        self.assertTrue(_bad(self.s, self._c(r)))

    def test_r2_03_portable_target_for_local_resources(self):
        for bad in BAD_PATHS:
            self.assertTrue(_bad(self.s, self._c(dict(self.e02, target=bad))), bad)
            self.assertTrue(_bad(self.s, self._c(dict(self.e03, target=bad))), bad)
        # 外部 resource の target は自由文字列（spreadsheet ID 等）
        ext = {"id": "e08", "resource": "external-document", "target": "1ToYHnmYHmVm25rqO1oLDE", "op": "read", "reversibility": "none", "idempotency": "idempotent", "human_action": "none",
               "network": {"endpoint": "sheets.googleapis.com", "direction": "read"}, "credential_locator": "service_account.json", "freshness": "t", "completeness": "c"}
        _ok(self.s, self._c(ext))


if __name__ == "__main__":
    unittest.main()
