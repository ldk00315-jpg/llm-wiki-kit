# -*- coding: utf-8 -*-
"""distill schema 4枚の型検査と、レビュー（merge 1・M-01〜M-05）で提示された negative instance の拒否を固定する。
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

    def _opp(self, **trigger):
        t = {"trigger_source": "scheduled", "trigger_ref": "run-1", "task_metadata_status": "unverifiable",
             "unverifiable_reason": "no adapter"}
        t.update(trigger)
        return dict(self.base, event_type="opportunity", opportunity_id="op-20260904T090000Z-0badcafe", trigger=t)

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

    def _page(self):
        return {"subject_type": "page", "distill_id": "d-0badcafe", "page_path": "wiki/concepts/X.md", "page_sha256": SHA}

    def test_m02_registered_is_non_state_event(self):
        r = dict(self.base, event_type="registered", subject=self._page(), source="human", actor="tonsuke", reason="register")
        _ok(self.s, r)
        self.assertTrue(_bad(self.s, dict(r, expected_previous_state="absent", new_state="nominated")))

    def test_m02_observed_is_system_and_needs_threshold(self):
        o = dict(self.base, event_type="observed", subject=self._page(), source="system", actor="wiki-health",
                 expected_previous_state="absent", new_state="observed",
                 threshold={"window_days": 30, "min_opportunities": 3, "counted_event_ids": ["a", "b", "c"]})
        _ok(self.s, o)
        self.assertTrue(_bad(self.s, dict(o, source="human")))
        o2 = dict(o); o2.pop("threshold"); self.assertTrue(_bad(self.s, o2))

    def test_m02_nominated_transitions(self):
        n = dict(self.base, event_type="nominated", subject=self._page(), source="human", actor="tonsuke", reason="x",
                 expected_previous_state="absent", new_state="nominated")
        _ok(self.s, n)
        self.assertTrue(_bad(self.s, dict(n, expected_previous_state="observed")))  # previous 束縛なし
        _ok(self.s, dict(n, expected_previous_state="observed", previous_event_id="20260904T080000Z-0badcafe", previous_event_sha256=SHA))
        self.assertTrue(_bad(self.s, dict(n, new_state="accepted")))

    def test_m02_decision_always_binds_previous(self):
        d = dict(self.base, event_type="decision", subject=self._page(), source="human", actor="tonsuke", reason="ok",
                 expected_previous_state="nominated", new_state="accepted")
        self.assertTrue(_bad(self.s, d))
        _ok(self.s, dict(d, previous_event_id="20260904T080000Z-0badcafe", previous_event_sha256=SHA))


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

    def test_proposed_ok(self):
        _ok(self.s, self.base)

    def test_m03_approved_without_binding_rejected(self):
        m = dict(self.base, decision={"status": "approved"})
        self.assertTrue(_bad(self.s, m))
        m = dict(self.base, decision={"status": "approved", "by": "t", "at": UTC, "reason": "r", "given_via": "chat"})
        self.assertTrue(_bad(self.s, m))  # bound_to 無し
        m = dict(self.base, decision={"status": "approved", "by": "t", "at": UTC, "reason": "r", "given_via": "chat", "bound_to": self._bound()})
        _ok(self.s, m)

    def test_m03_deployed_requires_approved_and_bundles(self):
        dep = {"claude": {"deployment_id": "x", "deployed_at": UTC, "files": [{"logical_path": "claude/SKILL.md", "deployed_path": "scheduled-tasks/x/SKILL.md", "sha256": SHA}]}}
        m = dict(self.base, state="deployed", deployed_bundles=dep)
        self.assertTrue(_bad(self.s, m))  # decision 未承認
        m["decision"] = {"status": "approved", "by": "t", "at": UTC, "reason": "r", "given_via": "chat", "bound_to": self._bound()}
        _ok(self.s, m)
        self.assertTrue(_bad(self.s, dict(m, deployed_bundles=None)))
        self.assertTrue(_bad(self.s, dict(self.base, deployed_bundles=dep)))  # proposed なのに bundles

    def test_m03_rejected_requires_reject_decision(self):
        self.assertTrue(_bad(self.s, dict(self.base, state="rejected")))
        _ok(self.s, dict(self.base, state="rejected", decision={"status": "rejected", "by": "t", "at": UTC, "reason": "no", "given_via": "chat", "bound_to": self._bound()}))

    def test_m04_zero_thresholds_rejected(self):
        o = dict(self.base["observation"], min_eligible_opportunities=0)
        self.assertTrue(_bad(self.s, dict(self.base, observation=o)))

    def test_m04_pass_requires_audit_fields_and_counts(self):
        o = dict(self.base["observation"], verdict="pass")
        self.assertTrue(_bad(self.s, dict(self.base, observation=o)))
        audit = dict(o, verdict_by="t", verdict_at=UTC, verdict_reason="ok", window={"start": UTC, "end": UTC},
                     eligible_count=3, completed_count=2, blocked_count=1, unterminated_count=0, terminal_conflicts=0,
                     event_set={"head_event_id": "20260904T090000Z-0badcafe", "events_sha256": SHA, "evidence": {"path": "distill/x/events-2026-09.json", "sha256": SHA}})
        _ok(self.s, dict(self.base, observation=audit))
        self.assertTrue(_bad(self.s, dict(self.base, observation=dict(audit, eligible_count=0, completed_count=0))))
        self.assertTrue(_bad(self.s, dict(self.base, observation=dict(audit, terminal_conflicts=1))))
        self.assertTrue(_bad(self.s, dict(self.base, observation=dict(audit, unterminated_count=1))))

    def test_m06_absolute_paths_rejected(self):
        for bad in ("C:/x/y.md", "/abs/y.md", "~/y.md", "a\\b.md"):
            m = json.loads(json.dumps(self.base)); m["wiki_refs"][0]["path"] = bad
            self.assertTrue(_bad(self.s, m), bad)
        m = json.loads(json.dumps(self.base)); m["wiki_refs"][0].pop("base_id")
        self.assertTrue(_bad(self.s, m))


@unittest.skipIf(V is None, "jsonschema not installed")
class TestEffectContract(unittest.TestCase):
    def setUp(self):
        self.s = _load("effect-contract.schema.json")
        self.head = {"contract_version": "0.1", "skill_slug": "monthly-listing-recon-csv",
                     "attestation": {"declared_by": "claude", "declared_at": UTC,
                                     "guarantee_ceiling": "review+probe+sandbox: undeclared effects found there fail validation; no runtime deny claimed"}}

    def _c(self, *effects):
        return dict(self.head, effects=list(effects))

    def test_m05_candidate_a_effects_validate(self):
        e01 = {"id": "e01", "resource": "human", "target": "Seller Hub CSV", "op": "human_action", "reversibility": "none", "idempotency": "idempotent", "human_action": "required"}
        e02 = {"id": "e02", "resource": "local-file", "target": "runs/<run>/input/active-listings.csv", "op": "read", "reversibility": "none", "idempotency": "idempotent", "human_action": "none",
               "local_io": {"kind": "input", "sensitivity": "high", "retention": "deleted by e06", "redaction": "ItemID hashed"}, "freshness": "mtime this month", "completeness": "rows>0"}
        e04 = {"id": "e04", "resource": "local-file", "target": "runs/<run>/report.json", "op": "create", "reversibility": "recreate_from_source", "idempotency": "keyed", "human_action": "none",
               "local_io": {"kind": "output", "sensitivity": "high", "retention": "90d, deleted by e07", "redaction": "evidence keeps sha256+count only"}, "postcondition": "rows+schema"}
        e06 = {"id": "e06", "resource": "local-file", "target": "runs/<run>/input/*.csv", "op": "delete", "reversibility": "none", "idempotency": "idempotent", "human_action": "none",
               "actor": "skill", "trigger": "next prepare", "precondition": "terminal completed or blocked>=30d", "postcondition": "path absent + delete event",
               "local_io": {"kind": "input", "sensitivity": "high", "retention": "n/a", "redaction": "n/a"}, "irreversible_ack": True}
        _ok(self.s, self._c(e01, e02, e04, e06))
        # create は backup を持たない
        self.assertTrue(_bad(self.s, self._c(dict(e04, backup={"method": "x", "taken_before_write": True, "scope": "y"}))))
        # write は backup 必須（形だけ無しは不可）
        self.assertTrue(_bad(self.s, self._c(dict(e04, op="write"))))
        # delete は backup か irreversible_ack のどちらか必須
        d = dict(e06); d.pop("irreversible_ack"); self.assertTrue(_bad(self.s, self._c(d)))
        # delete の actor/trigger/precondition 必須
        d = dict(e06); d.pop("actor"); self.assertTrue(_bad(self.s, self._c(d)))

    def test_read_requires_freshness_and_completeness(self):
        r = {"id": "e02", "resource": "local-db", "target": "openlister", "op": "read", "reversibility": "none", "idempotency": "idempotent", "human_action": "none", "freshness": "t"}
        self.assertTrue(_bad(self.s, self._c(r)))
        _ok(self.s, self._c(dict(r, completeness="n>0")))


if __name__ == "__main__":
    unittest.main()
