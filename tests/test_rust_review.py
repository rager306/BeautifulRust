#!/usr/bin/env python3
"""Tests for skills/rust-review/src/rust_review."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "skills" / "rust-review" / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

import rust_review  # noqa: E402


LIB_RS = """\
pub struct Config {
    pub name: String,
}

pub fn parse(input: &str) -> Config {
    let value = input.parse().unwrap();
    let config = Config { name: value };
    let _copy = config.name.clone();
    let _slot = unsafe { std::mem::zeroed::<u8>() };
    Config { name: String::new() }
}

#[cfg(test)]
mod tests {
    #[test]
    fn parses() {
        let _ = "1".parse::<u8>().unwrap();
    }
}

#[test]
fn inline_test_unwrap() {
    let _ = "2".parse::<u8>().unwrap();
}
"""

TEST_RS = """\
#[test]
fn integration() {}
"""


class RustReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "lib.rs").write_text(LIB_RS, encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "it.rs").write_text(TEST_RS, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    async def test_inventory_finds_markers(self):
        output = await rust_review.run(str(self.root))
        self.assertIn("Rust review inventory", output)
        self.assertIn("src/lib.rs", output)
        self.assertIn("public API", output)
        self.assertIn("- `unwrap()`: 1", output)
        self.assertIn("unsafe blocks", output)
        self.assertIn("inline tests excluded", output)
        self.assertIn("test_markers 3", output)
        self.assertIn("test files skipped", output)
        self.assertNotIn("tests/it.rs", output)

    async def test_include_inline_tests(self):
        output = await rust_review.run(str(self.root), include_inline_tests=True)
        self.assertIn("- `unwrap()`: 3", output)
        self.assertIn("inline tests included", output)
        self.assertNotIn("inline tests excluded", output)

    async def test_include_tests(self):
        output = await rust_review.run(str(self.root), include_tests=True)
        self.assertIn("tests/it.rs", output)
        self.assertIn("test markers", output)
        self.assertNotIn("test files skipped", output)

    async def test_max_files_truncates(self):
        output = await rust_review.run(str(self.root), include_tests=True, max_files=1)
        self.assertIn("more files were not read", output)

    async def test_missing_path(self):
        output = await rust_review.run(str(self.root / "missing"))
        self.assertIn("path does not exist", output)

    async def test_single_file(self):
        output = await rust_review.run(str(self.root / "src" / "lib.rs"))
        self.assertIn("## `lib.rs`", output)
        self.assertNotIn("tests/it.rs", output)


if __name__ == "__main__":
    unittest.main()
