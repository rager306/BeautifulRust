#!/usr/bin/env python3
"""Tests for allspark/check.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "allspark" / "check.py"
CORE = ("rust-best-practices", "rust-review", "rust-port")

VALID_PYPROJECT = """\
[project]
name = "rust-review"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []

[project.scripts]
rust_review = "rlm.skill:cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/rust_review"]
"""


def load_checker():
    spec = importlib.util.spec_from_file_location("allspark_check", SCRIPT)
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
    return path


def skill(name, description="A valid skill for the fixture.", extra=""):
    return (
        "---\n"
        "name: %s\n"
        "description: >-\n"
        "  %s\n"
        "%s"
        "---\n\n"
        "# %s\n" % (name, description, extra, name)
    )


def build_repo(root, names=CORE):
    root = Path(root)
    for name in names:
        write(root / "skills" / name / "SKILL.md", skill(name))
    return root


def add_python_package(root, name="rust-review", pyproject=VALID_PYPROJECT):
    directory = Path(root) / "skills" / name
    write(directory / "pyproject.toml", pyproject)
    import_name = name.replace("-", "_")
    write(directory / "src" / import_name / "__init__.py", "async def run():\n    return 'ok'\n")
    return directory


def messages(problems, level=None):
    return [
        problem.message
        for problem in problems
        if level is None or problem.level == level
    ]


class AllsparkCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()

    def test_real_repo_passes(self):
        names, python_skills, problems = self.checker.check_repo(REPO_ROOT)
        self.assertEqual(sorted(CORE), names)
        self.assertEqual(["rust-review"], python_skills)
        self.assertEqual([], messages(problems, "error"))
        self.assertEqual([], messages(problems, "warning"))

    def test_missing_description_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            write(
                root / "skills" / "rust-review" / "SKILL.md",
                "---\nname: rust-review\n---\n\n# Review\n",
            )
            _, _, problems = self.checker.check_repo(root)
            self.assertIn("description is required; allspark silently skips a skill without one", messages(problems, "error"))

    def test_name_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            write(
                root / "skills" / "rust-review" / "SKILL.md",
                "---\nname: other-name\ndescription: A valid skill.\n---\n",
            )
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("does not match directory" in message for message in messages(problems, "error")))

    def test_long_description_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            write(
                root / "skills" / "rust-review" / "SKILL.md",
                "---\nname: rust-review\ndescription: %s\n---\n" % ("x" * 1025),
            )
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("allspark allows 1024" in message for message in messages(problems, "error")))

    def test_disable_model_invocation_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            write(
                root / "skills" / "rust-review" / "SKILL.md",
                skill("rust-review", extra="disable-model-invocation: true\n"),
            )
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("hides the skill" in message for message in messages(problems, "error")))

    def test_claude_only_construct_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            write(
                root / "skills" / "rust-review" / "SKILL.md",
                skill("rust-review") + "\nRun ${CLAUDE_PLUGIN_ROOT}/bin/check.\n",
            )
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("CLAUDE_PLUGIN_ROOT" in message for message in messages(problems, "error")))

    def test_nested_skill_under_evals_is_not_discovered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            write(root / "skills" / "rust-review" / "evals" / "bad" / "SKILL.md", skill("bad"))
            names, _, problems = self.checker.check_repo(root)
            self.assertEqual(sorted(CORE), names)
            self.assertFalse(any("bad" in message for message in messages(problems, "error")))

    def test_missing_core_skill_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp, names=("rust-review", "rust-port"))
            _, _, problems = self.checker.check_repo(root)
            self.assertIn("core skill rust-best-practices is missing", messages(problems, "error"))

    def test_package_manifest_override_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            write(
                root / "package.json",
                '{"name": "demo", "pi": {"skills": ["other"]}}',
            )
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("does not point at skills/" in message for message in messages(problems, "error")))

    def test_python_skill_contract_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            add_python_package(root)
            _, python_skills, problems = self.checker.check_repo(root)
            self.assertEqual(["rust-review"], python_skills)
            self.assertEqual([], messages(problems, "error"))
            self.assertEqual([], messages(problems, "warning"))

    def test_python_skill_missing_package_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            write(root / "skills" / "rust-review" / "pyproject.toml", VALID_PYPROJECT)
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("missing src/rust_review/__init__.py" in message for message in messages(problems, "error")))

    def test_python_skill_runtime_dependency_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            add_python_package(
                root,
                pyproject=VALID_PYPROJECT.replace(
                    "dependencies = []", 'dependencies = ["prime-agent-runtime"]'
                ),
            )
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("prime-agent-runtime" in message for message in messages(problems, "error")))

    def test_python_skill_cli_name_warns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            add_python_package(
                root,
                pyproject=VALID_PYPROJECT.replace(
                    "rust_review = \"rlm.skill:cli\"", "review = \"rlm.skill:cli\""
                ),
            )
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("must match Python import name" in message for message in messages(problems, "warning")))

    def test_protected_edit_classifier(self):
        problems = self.checker._protected_edit_problems(
            [
                "README.md",
                "allspark/check.py",
                "FORK.md",
                "tests/test_rust_review.py",
                "skills/rust-review/src/rust_review/__init__.py",
                ".github/workflows/allspark-check.yml",
                "skills/rust-best-practices/SKILL.md",
                "CHANGELOG.md",
            ],
            {"README.md"},
        )
        self.assertEqual(2, len(problems))
        self.assertTrue(any("skills/rust-best-practices/SKILL.md" in problem for problem in problems))
        self.assertTrue(any("CHANGELOG.md" in problem for problem in problems))

    def test_nested_allspark_home_warns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".allspark-home" / ".allspark-home"
            build_repo(root)
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(
                any("nested .allspark-home" in message for message in messages(problems, "warning"))
            )

    def test_python_skill_invalid_toml_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_repo(temp)
            add_python_package(root, pyproject="[project\nname = 'broken'\n")
            _, _, problems = self.checker.check_repo(root)
            self.assertTrue(any("not valid TOML" in message for message in messages(problems, "error")))


if __name__ == "__main__":
    unittest.main()
