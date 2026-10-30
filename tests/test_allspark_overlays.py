#!/usr/bin/env python3
"""Tests for allspark/overlays.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "allspark" / "overlays.py"


def load_overlays():
    spec = importlib.util.spec_from_file_location("allspark_overlays", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fixture(root):
    write(
        Path(root) / "skills" / "rust-review" / "SKILL.md",
        "# Rust review\n\n## 5. Offer the lints\n\nSuggest the lints config.\n",
    )
    write(Path(root) / "README.md", "# Demo\n\n### allspark\n\nInstall it.\n")
    return Path(root)


class OverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overlays = load_overlays()

    def test_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = fixture(temp)
            self.assertEqual(
                ["skills/rust-review/SKILL.md", "README.md"], self.overlays.apply(root)
            )
            self.assertEqual([], self.overlays.apply(root))
            self.assertEqual([], self.overlays.check(root))
            self.assertIn("allspark:review-policy", (root / "skills" / "rust-review" / "SKILL.md").read_text())
            self.assertIn("allspark:reload-policy", (root / "README.md").read_text())

    def test_marker_is_restored_from_anchor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = fixture(temp)
            self.overlays.apply(root)
            skill = root / "skills" / "rust-review" / "SKILL.md"
            text = skill.read_text()
            text = text.replace("<!-- allspark:review-policy -->", "")
            skill.write_text(text)
            self.assertEqual(["skills/rust-review/SKILL.md"], self.overlays.apply(root))
            self.assertIn("<!-- allspark:review-policy -->", skill.read_text())

    def test_missing_anchor_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = fixture(temp)
            skill = root / "skills" / "rust-review" / "SKILL.md"
            skill.write_text("# Rust review\n\n## Changed heading\n")
            with self.assertRaises(self.overlays.OverlayError):
                self.overlays.apply(root)

    def test_check_reports_missing_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = fixture(temp)
            problems = self.overlays.check(root)
            self.assertEqual(2, len(problems))

    def test_real_repo_is_applied(self):
        self.assertEqual([], self.overlays.check(REPO_ROOT))


if __name__ == "__main__":
    unittest.main()
