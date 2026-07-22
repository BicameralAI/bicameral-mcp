import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "resolve_atlas_assignment_event.py"
SPEC = importlib.util.spec_from_file_location("resolve_atlas_assignment_event", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ResolveAtlasAssignmentEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.head = "a" * 40
        self.base = "b" * 40
        self.pull = {
            "number": 918,
            "state": "open",
            "head": {"sha": self.head},
            "base": {"sha": self.base, "ref": "dev"},
        }

    def test_selects_one_exact_open_dev_pull_request(self) -> None:
        self.assertEqual(
            MODULE.select_pull_request([self.pull], self.head),
            {"pull_request": 918, "head_sha": self.head, "base_sha": self.base},
        )

    def test_rejects_ambiguous_or_wrong_base_pull_request(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            MODULE.select_pull_request([self.pull, self.pull], self.head)
        wrong = {**self.pull, "base": {"sha": self.base, "ref": "main"}}
        with self.assertRaisesRegex(ValueError, "exactly one"):
            MODULE.select_pull_request([wrong], self.head)

    def test_release_unit_paths_exclude_removed_and_fail_closed(self) -> None:
        files = [
            {"filename": "release-units/b.json", "status": "modified"},
            {"filename": "release-units/a.json", "status": "added"},
            {"filename": "release-units/old.json", "status": "removed"},
            {"filename": "src/main.rs", "status": "modified"},
        ]
        self.assertEqual(
            MODULE.release_unit_paths(files),
            ["release-units/a.json", "release-units/b.json"],
        )
        with self.assertRaisesRegex(ValueError, "1-20"):
            MODULE.release_unit_paths([{"filename": "src/main.rs", "status": "added"}])
        with self.assertRaisesRegex(ValueError, "unsafe"):
            MODULE.release_unit_paths(
                [{"filename": "release-units/nested/unit.json", "status": "added"}]
            )

    def test_github_outputs_are_single_line_and_compact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            MODULE.append_github_outputs(
                output,
                {"pull_request": 918, "proposal_paths_json": ["release-units/a.json"]},
            )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                'pull_request=918\nproposal_paths_json=["release-units/a.json"]\n',
            )

    def test_github_reader_rejects_noncanonical_api_origin(self) -> None:
        with self.assertRaisesRegex(ValueError, "https://api.github.com"):
            MODULE.GitHubReader("file:///tmp/github", "token")


if __name__ == "__main__":
    unittest.main()
