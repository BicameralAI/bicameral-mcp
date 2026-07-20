from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("emit_release_descriptor.py")
SPEC = importlib.util.spec_from_file_location("mcp_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DescriptorTests(unittest.TestCase):
    def test_descriptor_is_content_addressed(self) -> None:
        payload = MODULE.build_descriptor("a" * 40)
        self.assertEqual([], MODULE.validate_descriptor(payload))
        payload["interfaces"]["tool_request"] = "sha256:" + "b" * 64
        self.assertIn(
            "descriptor_digest does not bind the descriptor", MODULE.validate_descriptor(payload)
        )


if __name__ == "__main__":
    unittest.main()
