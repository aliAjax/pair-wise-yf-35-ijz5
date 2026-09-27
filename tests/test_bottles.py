import tempfile
import unittest
from pathlib import Path

from src.domain import Actor, PermissionDenied, ValidationError
from src.repository import SQLiteRepository
from src.rules import RuleEngine
from src.service import DomainService


class BottleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.tmp.name) / "test.db")
        self.service = DomainService(self.repo, RuleEngine())
        self.admin = Actor("admin-1", "admin")
        self.lab = Actor("lab-1", "lab")

    def tearDown(self):
        self.tmp.cleanup()

    def _received_sample(self):
        athlete = self.service.create(
            self.admin, "athlete", {"name": "Rider", "discipline": "cycling"}
        )
        sample = self.service.create(
            self.admin,
            "sample",
            {"athlete_id": athlete["id"], "sample_code": "S-100", "event": "final"},
        )
        self.service.transition(
            self.admin, sample["id"], "collect", {"collected_at": "2026-01-01T08:00:00Z"}
        )
        self.service.transition(
            self.admin, sample["id"], "seal", {"seal_a": "SEAL-A-1", "seal_b": "SEAL-B-1"}
        )
        self.service.transition(self.admin, sample["id"], "ship", {"carrier": "Courier-A"})
        self.service.transition(self.admin, sample["id"], "receive", {"lab_id": "LAB-1"})
        return sample["id"]

    def _adverse_sample(self):
        sample_id = self._received_sample()
        self.service.transition(self.lab, sample_id, "analyze", {"result": "adverse"})
        self.service.transition(self.lab, sample_id, "report_adverse", {})
        return sample_id

    def test_seal_registers_both_bottles(self):
        sample_id = self._received_sample()
        view = self.service.get(sample_id)["bottle_view"]
        self.assertEqual(view["a"]["seal_id"], "SEAL-A-1")
        self.assertEqual(view["b"]["seal_id"], "SEAL-B-1")
        self.assertEqual(view["a"]["status"], "sealed")
        self.assertEqual(view["b"]["status"], "sealed")

    def test_seal_requires_distinct_seal_numbers(self):
        athlete = self.service.create(
            self.admin, "athlete", {"name": "Rider", "discipline": "cycling"}
        )
        sample = self.service.create(
            self.admin,
            "sample",
            {"athlete_id": athlete["id"], "sample_code": "S-101", "event": "final"},
        )
        self.service.transition(
            self.admin, sample["id"], "collect", {"collected_at": "2026-01-01"}
        )
        with self.assertRaises(ValidationError):
            self.service.transition(
                self.admin, sample["id"], "seal", {"seal_a": "SAME", "seal_b": "SAME"}
            )

    def test_analyze_opens_only_a_bottle(self):
        sample_id = self._received_sample()
        self.service.transition(self.lab, sample_id, "analyze", {"result": "negative"})
        entity = self.service.get(sample_id)
        view = entity["bottle_view"]
        self.assertEqual(view["a"]["status"], "opened")
        self.assertEqual(view["a"]["opened_by"], "lab-1")
        self.assertEqual(view["b"]["status"], "sealed")
        records = entity["data"]["unseal_records"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["bottle"], "A")
        self.assertEqual(record["seal_id"], "SEAL-A-1")
        self.assertEqual(record["opened_by"], "lab-1")
        self.assertTrue(record["opened_at"])

    def test_cleared_sample_keeps_b_sealed(self):
        sample_id = self._received_sample()
        self.service.transition(self.lab, sample_id, "analyze", {"result": "negative"})
        self.service.transition(self.lab, sample_id, "clear", {"reason": "normal"})
        view = self.service.get(sample_id)["bottle_view"]
        self.assertEqual(view["b"]["status"], "sealed")
        self.assertEqual(view["b"]["note"], "A瓶结果正常，B瓶继续封存")
        self.assertEqual(len(view["unseal_records"]), 1)

    def test_open_b_requires_request_and_two_witnesses(self):
        sample_id = self._adverse_sample()
        with self.assertRaises(ValidationError) as ctx:
            self.service.transition(self.admin, sample_id, "open_b", {})
        self.assertIn("B瓶复检未发起", str(ctx.exception))

        self.service.transition(
            self.admin, sample_id, "request_b_retest", {"reason": "A瓶异常"}
        )
        with self.assertRaises(ValidationError) as ctx:
            self.service.transition(self.admin, sample_id, "open_b", {})
        self.assertIn("0/2", str(ctx.exception))

        self.service.transition(
            self.admin, sample_id, "register_witness", {"witness": "见证人甲"}
        )
        with self.assertRaises(ValidationError) as ctx:
            self.service.transition(self.admin, sample_id, "open_b", {})
        self.assertIn("1/2", str(ctx.exception))

        view = self.service.get(sample_id)["bottle_view"]
        self.assertEqual(view["b"]["status"], "sealed")
        self.assertTrue(view["b"]["missing"])

        self.service.transition(
            self.admin, sample_id, "register_witness", {"witness": "见证人乙"}
        )
        updated = self.service.transition(self.admin, sample_id, "open_b", {})
        self.assertEqual(updated["status"], "b_opened")

        entity = self.service.get(sample_id)
        view = entity["bottle_view"]
        self.assertEqual(view["b"]["status"], "opened")
        self.assertEqual(view["b"]["witnesses"], ["见证人甲", "见证人乙"])
        b_records = [r for r in entity["data"]["unseal_records"] if r["bottle"] == "B"]
        self.assertEqual(len(b_records), 1)
        record = b_records[0]
        self.assertEqual(record["seal_id"], "SEAL-B-1")
        self.assertEqual(record["opened_by"], "admin-1")
        self.assertEqual(record["witnesses"], ["见证人甲", "见证人乙"])
        self.assertTrue(record["opened_at"])

    def test_duplicate_witness_rejected(self):
        sample_id = self._adverse_sample()
        self.service.transition(self.admin, sample_id, "request_b_retest", {})
        self.service.transition(
            self.admin, sample_id, "register_witness", {"witness": "甲"}
        )
        with self.assertRaises(ValidationError):
            self.service.transition(
                self.admin, sample_id, "register_witness", {"witness": "甲"}
            )

    def test_request_b_retest_requires_admin(self):
        sample_id = self._adverse_sample()
        with self.assertRaises(PermissionDenied):
            self.service.transition(self.lab, sample_id, "request_b_retest", {})

    def test_record_b_result(self):
        sample_id = self._adverse_sample()
        self.service.transition(self.admin, sample_id, "request_b_retest", {})
        self.service.transition(
            self.admin, sample_id, "register_witness", {"witness": "甲"}
        )
        self.service.transition(
            self.admin, sample_id, "register_witness", {"witness": "乙"}
        )
        self.service.transition(self.admin, sample_id, "open_b", {})
        updated = self.service.transition(
            self.lab, sample_id, "record_b_result", {"b_result": "confirmed"}
        )
        self.assertEqual(updated["status"], "b_closed")
        self.assertEqual(updated["data"]["b_result"], "confirmed")


if __name__ == "__main__":
    unittest.main()
