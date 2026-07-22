import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/atlas-release-assignment-status.yml"


class AtlasReleaseAssignmentStatusWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_only_exact_pending_assignment_status_enters_gate(self) -> None:
        self.assertIn("status:", self.workflow)
        self.assertIn("github.event.context == 'release-unit / assigned'", self.workflow)
        self.assertIn("github.event.state == 'pending'", self.workflow)
        self.assertIn("name: release-unit / assigned", self.workflow)
        self.assertNotIn("--raw-field state=pending", self.workflow)

    def test_candidate_cannot_replace_resolver_or_factory_verifier(self) -> None:
        self.assertIn("ref: main\n          path: policy-host", self.workflow)
        self.assertIn("ref: ${{ github.event.sha }}\n          path: candidate", self.workflow)
        match = re.search(r"FACTORY_COMMIT: ([0-9a-f]{40})", self.workflow)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), "a45ea7c0ab912e71acca61500533a6528e309972")
        self.assertIn("_release-policy/scripts/atlas_assignment_gate.py", self.workflow)

    def test_receipt_reader_is_event_driven_and_read_only(self) -> None:
        self.assertIn("pull-requests: read", self.workflow)
        self.assertIn("id-token: write", self.workflow)
        self.assertIn("statuses: write", self.workflow)
        self.assertNotIn("actions: write", self.workflow)
        self.assertNotIn("contents: write", self.workflow)
        self.assertIn("atlas-receipts-mcp@", self.workflow)
        self.assertIn("bicameral-mcp-atlas-receipts", self.workflow)
        self.assertIn("gcloud storage cp", self.workflow)
        self.assertNotIn("schedule:", self.workflow)

    def test_complete_set_verification_precedes_evidence_retention(self) -> None:
        verify = self.workflow.index("Verify complete signed exact assignment set")
        upload = self.workflow.index("Retain verified assignment evidence")
        self.assertLess(verify, upload)
        self.assertIn("--proposal-paths-json", self.workflow)
        self.assertIn("--base-sha", self.workflow)
        self.assertIn("fetch-depth: 0", self.workflow)
        self.assertIn("retention-days: 90", self.workflow)
        success_status = self.workflow.index("Publish exact verified assignment status")
        self.assertGreater(success_status, upload)
        self.assertIn("statuses/${STATUS_SHA}", self.workflow)
        self.assertIn("state=success", self.workflow)
        self.assertIn("state=failure", self.workflow)
        self.assertEqual(4, self.workflow.count("release-unit / assigned"))


if __name__ == "__main__":
    unittest.main()
