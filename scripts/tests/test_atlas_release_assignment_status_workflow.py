import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/atlas-release-assignment-status.yml"
FACTORY_COMMIT = "6ec555cf02078e5d937d086055d435573655573a"


class AtlasReleaseAssignmentStatusWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_only_canonical_pending_release_unit_statuses_enter_gate(self) -> None:
        self.assertIn("status:", self.workflow)
        self.assertIn("github.event.context == 'release-unit / assigned'", self.workflow)
        self.assertIn("github.event.context == 'release-unit / merge-eligible'", self.workflow)
        self.assertIn("github.event.state == 'pending'", self.workflow)
        self.assertNotIn("schedule:", self.workflow)

    def test_factory_resolver_and_policy_use_one_immutable_commit(self) -> None:
        uses = re.search(r"atlas-release-unit-status\.yml@([0-9a-f]{40})", self.workflow)
        factory_ref = re.search(r"factory_ref: ([0-9a-f]{40})", self.workflow)
        self.assertIsNotNone(uses)
        self.assertIsNotNone(factory_ref)
        assert uses and factory_ref
        self.assertEqual(FACTORY_COMMIT, uses.group(1))
        self.assertEqual(FACTORY_COMMIT, factory_ref.group(1))

    def test_wrapper_grants_only_read_receipt_and_status_permissions(self) -> None:
        self.assertIn("pull-requests: read", self.workflow)
        self.assertIn("id-token: write", self.workflow)
        self.assertIn("statuses: write", self.workflow)
        self.assertNotIn("actions: write", self.workflow)
        self.assertNotIn("contents: write", self.workflow)
        self.assertIn("atlas-receipts", self.workflow)
        self.assertIn("BICAMERAL_CROSS_REPO_TOKEN", self.workflow)
        self.assertNotIn("gcloud storage", self.workflow)
        self.assertNotIn("gh api", self.workflow)


if __name__ == "__main__":
    unittest.main()
