import tempfile
import unittest
from pathlib import Path

from src.domain import (
    Actor,
    InvalidTransition,
    PermissionDenied,
    ValidationError,
)
from src.repository import SQLiteRepository
from src.rules import RuleEngine
from src.service import DomainService


class DualBottleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.tmp.name) / "test.db")
        self.service = DomainService(self.repo, RuleEngine())
        self.admin = Actor("admin-1", "admin")
        self.inspector = Actor("dco-1", "inspector")
        self.lab = Actor("lab-1", "lab")
        self.sample_id = self._sealed_sample()

    def tearDown(self):
        self.tmp.cleanup()

    def _sealed_sample(self):
        athlete = self.service.create(
            self.admin,
            "athlete",
            {"name": "A. Rider", "discipline": "cycling"},
        )
        sample = self.service.create(
            self.inspector,
            "sample",
            {
                "athlete_id": athlete["id"],
                "sample_code": "S-DUAL-1",
                "event": "national-final",
            },
        )
        self.service.transition(
            self.inspector, sample["id"], "collect", {"collected_at": "2026-01-01T08:00:00Z"}
        )
        sample = self.service.transition(
            self.inspector,
            sample["id"],
            "seal",
            {"seal_id_a": "SEAL-A", "seal_id_b": "SEAL-B"},
        )
        return sample["id"]

    def _receive(self):
        self.service.transition(
            self.inspector, self.sample_id, "ship", {"carrier": "Courier-A"}
        )
        self.service.transition(self.lab, self.sample_id, "receive", {"lab_id": "LAB-1"})

    def _adverse_a(self):
        self._receive()
        return self.service.transition(
            self.lab, self.sample_id, "analyze", {"result": "adverse"}
        )

    def test_seal_registers_distinct_a_and_b_seal_numbers(self):
        sample = self.service.get(self.sample_id)
        self.assertEqual(sample["data"]["seal_id_a"], "SEAL-A")
        self.assertEqual(sample["data"]["seal_id_b"], "SEAL-B")
        bottles = sample["data"]["bottles"]
        self.assertEqual(bottles["A"], {"seal_id": "SEAL-A", "state": "sealed"})
        self.assertEqual(bottles["B"], {"seal_id": "SEAL-B", "state": "sealed"})

    def test_seal_requires_both_seal_numbers_and_they_must_differ(self):
        athlete = self.service.list("athlete")[0]["id"]
        for payload in (
            {"seal_id_a": "X1"},
            {"seal_id_b": "X2"},
            {"seal_id_a": "SAME", "seal_id_b": "SAME"},
        ):
            sample = self.service.create(
                self.inspector,
                "sample",
                {"athlete_id": athlete, "sample_code": "S-X", "event": "final"},
            )
            self.service.transition(
                self.inspector, sample["id"], "collect", {"collected_at": "2026-01-01"}
            )
            with self.assertRaises(ValidationError):
                self.service.transition(
                    self.inspector, sample["id"], "seal", payload
                )

    def test_daily_lab_only_opens_a_and_records_it(self):
        sample = self._adverse_a()
        self.assertEqual(sample["status"], "analyzed_a")
        self.assertEqual(sample["data"]["bottles"]["A"]["state"], "opened")
        # A 瓶结果正常时 B 瓶继续封存
        self.assertEqual(sample["data"]["bottles"]["B"]["state"], "sealed")
        records = sample["data"]["unseal_records"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["bottle"], "A")
        self.assertEqual(record["seal_id"], "SEAL-A")
        self.assertTrue(record["opened_at"])
        self.assertEqual(record["operator_id"], "lab-1")
        self.assertEqual(record["witnesses"], [])

    def test_normal_a_result_keeps_b_sealed_and_blocks_retest(self):
        self._receive()
        self.service.transition(
            self.lab, self.sample_id, "analyze", {"result": "normal"}
        )
        sample = self.service.get(self.sample_id)
        self.assertEqual(sample["data"]["bottles"]["B"]["state"], "sealed")
        with self.assertRaises(ValidationError):
            self.service.transition(
                self.admin, self.sample_id, "request_b_retest", {}
            )

    def test_retest_requires_admin_and_adverse_a(self):
        self._adverse_a()
        with self.assertRaises(PermissionDenied):
            self.service.transition(
                self.lab, self.sample_id, "request_b_retest", {}
            )
        self.service.transition(
            self.admin, self.sample_id, "report_adverse", {}
        )
        sample = self.service.transition(
            self.admin, self.sample_id, "request_b_retest", {}
        )
        self.assertEqual(sample["status"], "b_retest_requested")
        # 发起复检本身不启封 B 瓶
        self.assertEqual(sample["data"]["bottles"]["B"]["state"], "sealed")

    def test_b_stays_sealed_when_witness_paperwork_is_incomplete(self):
        self._adverse_a()
        self.service.transition(self.admin, self.sample_id, "report_adverse", {})
        self.service.transition(
            self.admin, self.sample_id, "request_b_retest", {}
        )
        for payload in (
            {},
            {"witnesses": []},
            {"witnesses": ["witness-1"]},
            {"witnesses": ["same", "same"]},
            {"witnesses": ["admin-1", "witness-2"]},
        ):
            with self.assertRaises(ValidationError) as ctx:
                self.service.transition(
                    self.admin, self.sample_id, "open_b", payload
                )
            # 手续不全时明确指出缺少哪一项
            self.assertTrue(str(ctx.exception), "error must name the missing item")
            sample = self.service.get(self.sample_id)
            # B 瓶保持封存
            self.assertEqual(sample["data"]["bottles"]["B"]["state"], "sealed")
            self.assertNotIn(
                "B",
                {
                    record["bottle"]
                    for record in sample["data"].get("unseal_records", [])
                },
            )

    def test_b_opens_with_two_witnesses_and_records_full_paperwork(self):
        self._adverse_a()
        self.service.transition(self.admin, self.sample_id, "report_adverse", {})
        self.service.transition(
            self.admin, self.sample_id, "request_b_retest", {}
        )
        sample = self.service.transition(
            self.inspector,
            self.sample_id,
            "open_b",
            {"witnesses": ["witness-1", "witness-2"]},
        )
        self.assertEqual(sample["status"], "b_opened")
        self.assertEqual(sample["data"]["bottles"]["B"]["state"], "opened")
        records = sample["data"]["unseal_records"]
        self.assertEqual([r["bottle"] for r in records], ["A", "B"])
        record_b = records[-1]
        self.assertEqual(record_b["seal_id"], "SEAL-B")
        self.assertTrue(record_b["opened_at"])
        self.assertEqual(record_b["operator_id"], "dco-1")
        self.assertEqual(record_b["witnesses"], ["witness-1", "witness-2"])

    def test_cannot_open_b_before_retest_is_requested(self):
        self._adverse_a()
        # 复检尚未发起，状态机不允许直接启封 B 瓶
        with self.assertRaises(InvalidTransition):
            self.service.transition(
                self.inspector,
                self.sample_id,
                "open_b",
                {"witnesses": ["w1", "w2"]},
            )

    def test_cannot_analyze_b_before_it_is_opened(self):
        self._adverse_a()
        with self.assertRaises(InvalidTransition):
            self.service.transition(
                self.lab, self.sample_id, "analyze_b", {"b_result": "normal"}
            )

    def test_audit_timeline_keeps_unseal_actions(self):
        self._adverse_a()
        self.service.transition(self.admin, self.sample_id, "report_adverse", {})
        self.service.transition(
            self.admin, self.sample_id, "request_b_retest", {}
        )
        self.service.transition(
            self.inspector,
            self.sample_id,
            "open_b",
            {"witnesses": ["witness-1", "witness-2"]},
        )
        actions = [
            entry["action"]
            for entry in self.service.audit_log(self.sample_id)
        ]
        self.assertIn("analyze", actions)  # A 瓶启封检测
        self.assertIn("open_b", actions)  # B 瓶启封


if __name__ == "__main__":
    unittest.main()
