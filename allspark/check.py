#!/usr/bin/env python3
"""Check that BeautifulRust's Agent Skills load in allspark (Prime Agent).

allspark discovers a capability package by its ``skills/<name>/SKILL.md`` files.
It follows the Agent Skills standard, so the upstream files are already close;
this checker covers the rules allspark enforces and the Claude-only constructs
that allspark ignores. It is deliberately stdlib-only so CI can run it without
installing anything:

    python3 allspark/check.py
    python3 allspark/check.py --json

Exit code 1 means the fork needs a manual adaptation before the next release.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CORE_SKILLS = ("rust-best-practices", "rust-review", "rust-port")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_SKILL_BYTES = 256 * 1024  # pa-core resources::MAX_CONTEXT_FILE_BYTES
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Constructs that only work in Claude Code's plugin runtime. They are fine in
# the README and in evals, but not in a skill the allspark agent will load.
CLAUDE_ONLY = (
    (re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}"), "CLAUDE_PLUGIN_ROOT is a Claude Code plugin variable"),
    (re.compile(r"\bclaude\s+plugin\b", re.IGNORECASE), "Claude Code plugin command"),
    (re.compile(r"/plugin\s+(?:install|marketplace)\b", re.IGNORECASE), "Claude Code /plugin command"),
    (re.compile(r"\.claude-plugin\b"), "Claude Code plugin path"),
    (re.compile(r"claude\s+--plugin-dir\b", re.IGNORECASE), "Claude Code --plugin-dir flag"),
)


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    level: str  # "error" or "warning"
    message: str


def _line_of(text: str, key: str) -> int:
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"^" + re.escape(key) + r"\s*:", line):
            return number
    return 1


def split_frontmatter(text: str) -> Tuple[Optional[str], int, str]:
    """Return (frontmatter, body start line, body).

    A missing block is different from an unterminated one: allspark treats both
    as no frontmatter, so the caller has to report the unterminated case.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, 1, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), index + 2, "\n".join(lines[index + 1 :])
    return None, 1, text


def _strip_comment(value: str) -> str:
    in_single = in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value.rstrip()


def parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse the top-level keys this checker needs from Agent Skills YAML.

    Supports plain and quoted scalars plus ``|``/``>`` blocks with chomping.
    Nested mappings are skipped: allspark ignores them for routing.
    """
    data: Dict[str, Any] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        if raw[:1].isspace():
            index += 1
            continue
        key, separator, rest = raw.partition(":")
        if not separator:
            index += 1
            continue
        key = key.strip()
        rest = rest.strip()
        if rest in (">", ">-", ">+", "|", "|-", "|+"):
            block: List[str] = []
            base_indent: Optional[int] = None
            index += 1
            while index < len(lines):
                line = lines[index]
                if not line.strip():
                    block.append("")
                    index += 1
                    continue
                indent = len(line) - len(line.lstrip(" "))
                if indent == 0:
                    break
                if base_indent is None:
                    base_indent = indent
                block.append(line[base_indent:])
                index += 1
            while block and block[-1] == "":
                block.pop()
            if rest.startswith(">"):
                paragraphs: List[str] = []
                current: List[str] = []
                for line in block:
                    if line == "":
                        if current:
                            paragraphs.append(" ".join(current))
                            current = []
                        paragraphs.append("")
                    else:
                        current.append(line)
                if current:
                    paragraphs.append(" ".join(current))
                value = "\n".join(paragraphs)
            else:
                value = "\n".join(block)
            if not rest.endswith("-"):
                value += "\n"
            data[key] = value
            continue
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in ("'", '"'):
            value = rest[1:-1]
            if rest[0] == "'":
                value = value.replace("''", "'")
            else:
                value = value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
            data[key] = value
        else:
            value = _strip_comment(rest)
            if value == "true":
                data[key] = True
            elif value == "false":
                data[key] = False
            elif value in ("null", "~"):
                data[key] = None
            else:
                data[key] = value
        index += 1
    return data


def discover_skill_files(root: Path) -> List[Path]:
    """Mirror allspark's Pi-mode discovery under a package's skills/ dir.

    A directory containing SKILL.md is a skill root; recursion stops there.
    Hidden directories and node_modules are skipped.
    """
    skills_dir = root / "skills"
    found: List[Path] = []

    def walk(directory: Path) -> None:
        if (directory / "SKILL.md").is_file():
            found.append(directory / "SKILL.md")
            return
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name == "node_modules":
                continue
            if entry.is_dir():
                walk(entry)

    if skills_dir.is_dir():
        walk(skills_dir)
    return found


def _check_text(path: Path, text: str, problems: List[Problem]) -> None:
    for pattern, message in CLAUDE_ONLY:
        match = pattern.search(text)
        if match:
            line = text[: match.start()].count("\n") + 1
            problems.append(Problem(str(path), line, "error", message))
    size = len(text.encode("utf-8"))
    if size > MAX_SKILL_BYTES:
        problems.append(
            Problem(
                str(path),
                1,
                "error",
                "file is %d bytes; allspark truncates skill files at %d bytes"
                % (size, MAX_SKILL_BYTES),
            )
        )


def _check_skill(skill_md: Path, root: Path, problems: List[Problem]) -> Optional[str]:
    relative = skill_md.relative_to(root)
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as error:
        problems.append(Problem(str(relative), 1, "error", str(error)))
        return None

    frontmatter_text, _body_line, _body = split_frontmatter(text)
    if frontmatter_text is None:
        if text.lstrip().startswith("---"):
            problems.append(Problem(str(relative), 1, "error", "unterminated frontmatter"))
        else:
            problems.append(Problem(str(relative), 1, "error", "missing frontmatter"))
        return None

    data = parse_frontmatter(frontmatter_text)
    parent_name = skill_md.parent.name
    name = str(data.get("name") or "")
    description = str(data.get("description") or "")

    if not name:
        problems.append(
            Problem(str(relative), _line_of(text, "name"), "error", "missing frontmatter name")
        )
        name = parent_name
    if name != parent_name:
        problems.append(
            Problem(
                str(relative),
                _line_of(text, "name"),
                "error",
                'name "%s" does not match directory "%s"' % (name, parent_name),
            )
        )
    if len(name) > MAX_NAME:
        problems.append(
            Problem(str(relative), _line_of(text, "name"), "error", "name exceeds %d characters" % MAX_NAME)
        )
    if not NAME_RE.match(name):
        problems.append(
            Problem(
                str(relative),
                _line_of(text, "name"),
                "error",
                "name must be lowercase a-z, 0-9, and single hyphens",
            )
        )
    if not description.strip():
        problems.append(
            Problem(
                str(relative),
                _line_of(text, "description"),
                "error",
                "description is required; allspark silently skips a skill without one",
            )
        )
    elif len(description) > MAX_DESCRIPTION:
        problems.append(
            Problem(
                str(relative),
                _line_of(text, "description"),
                "error",
                "description is %d characters; allspark allows %d"
                % (len(description), MAX_DESCRIPTION),
            )
        )
    if data.get("disable-model-invocation") is True:
        problems.append(
            Problem(
                str(relative),
                _line_of(text, "disable-model-invocation"),
                "error",
                "disable-model-invocation hides the skill from the allspark prompt",
            )
        )
    if "allowed-tools" in data:
        problems.append(
            Problem(
                str(relative),
                _line_of(text, "allowed-tools"),
                "warning",
                "allspark ignores allowed-tools; keep tool policy in the skill body",
            )
        )

    _check_text(skill_md, text, problems)
    references = skill_md.parent / "references"
    if references.is_dir():
        for reference in sorted(references.rglob("*.md")):
            try:
                reference_text = reference.read_text(encoding="utf-8")
            except OSError as error:
                problems.append(Problem(str(reference.relative_to(root)), 1, "error", str(error)))
                continue
            _check_text(reference, reference_text, problems)

    pyproject = skill_md.parent / "pyproject.toml"
    if pyproject.exists():
        import_name = name.replace("-", "_")
        package_init = skill_md.parent / "src" / import_name / "__init__.py"
        if package_init.is_file():
            problems.append(
                Problem(
                    str(relative),
                    1,
                    "error",
                    "pyproject.toml makes allspark install this as a Python skill; adapt the fork deliberately",
                )
            )
        else:
            problems.append(
                Problem(
                    str(relative),
                    1,
                    "warning",
                    "pyproject.toml without src/%s/__init__.py: allspark warns and loads it as markdown"
                    % import_name,
                )
            )
    return name


def _check_package_manifest(root: Path, problems: List[Problem]) -> None:
    package_json = root / "package.json"
    if not package_json.exists():
        return
    try:
        manifest = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        problems.append(Problem("package.json", 1, "warning", "cannot parse package.json: %s" % error))
        return
    if not isinstance(manifest, dict):
        problems.append(Problem("package.json", 1, "warning", "package.json is not an object"))
        return
    pi = manifest.get("pi")
    if not isinstance(pi, dict) or "skills" not in pi:
        return
    entries = pi.get("skills")
    if not isinstance(entries, list):
        problems.append(Problem("package.json", 1, "error", "pi.skills must be an array"))
        return
    covers_skills = any(
        isinstance(entry, str)
        and (
            entry.rstrip("/") in ("skills", "./skills")
            or entry.startswith("skills/")
            or entry.startswith("./skills/")
        )
        for entry in entries
    )
    if not covers_skills:
        problems.append(
            Problem(
                "package.json",
                1,
                "error",
                "pi.skills overrides allspark's skills/ convention but does not point at skills/",
            )
        )


def _plugin_version(root: Path) -> str:
    try:
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return str(manifest.get("version") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def _check_upstream_metadata(root: Path, problems: List[Problem]) -> None:
    meta_path = root / "allspark" / "upstream.json"
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        problems.append(Problem("allspark/upstream.json", 1, "warning", "cannot parse: %s" % error))
        return
    if not isinstance(meta, dict):
        return
    version = _plugin_version(root)
    recorded = str(meta.get("plugin_version") or "")
    if version and recorded and version != recorded:
        problems.append(
            Problem(
                "allspark/upstream.json",
                1,
                "warning",
                "plugin version %s does not match recorded %s" % (version, recorded),
            )
        )
    commit = str(meta.get("upstream_commit") or "")
    if commit and (root / ".git").exists():
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            problems.append(
                Problem(
                    "allspark/upstream.json",
                    1,
                    "warning",
                    "recorded upstream commit is not an ancestor of HEAD; run sync",
                )
            )


def check_repo(root: Path) -> Tuple[List[str], List[Problem]]:
    root = root.resolve()
    problems: List[Problem] = []
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        problems.append(Problem("skills", 1, "error", "missing skills/ directory"))
        return [], problems

    skill_files = discover_skill_files(root)
    if not skill_files:
        problems.append(Problem("skills", 1, "error", "no SKILL.md found under skills/"))
        return [], problems

    names: List[str] = []
    for skill_md in skill_files:
        name = _check_skill(skill_md, root, problems)
        if name:
            names.append(name)

    for name in CORE_SKILLS:
        if name not in names:
            problems.append(
                Problem("skills", 1, "error", "core skill %s is missing" % name)
            )

    seen: Dict[str, Path] = {}
    for skill_md in skill_files:
        if skill_md.parent.name in seen:
            problems.append(
                Problem(
                    str(skill_md.relative_to(root)),
                    1,
                    "error",
                    "duplicate skill name %s" % skill_md.parent.name,
                )
            )
        seen[skill_md.parent.name] = skill_md

    # allspark's Pi mode also loads direct .md children of the scan root.
    for stray in sorted(skills_dir.glob("*.md")):
        problems.append(
            Problem(
                str(stray.relative_to(root)),
                1,
                "warning",
                "root markdown file in skills/ is also loaded as a skill in Pi mode",
            )
        )

    _check_package_manifest(root, problems)
    _check_upstream_metadata(root, problems)
    return sorted(names), problems


def format_problem(problem: Problem) -> str:
    return "%s:%d: %s: %s" % (problem.path, problem.line, problem.level, problem.message)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check BeautifulRust compatibility with allspark")
    parser.add_argument("root", nargs="?", default=".", help="repository root (default: current directory)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="print machine-readable output")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        message = "%s: path does not exist" % args.root
        if args.as_json:
            print(json.dumps({"ok": False, "error_count": 1, "warning_count": 0,
                              "problems": [{"path": args.root, "line": 1, "level": "error",
                                            "message": "path does not exist"}]}, indent=2))
        else:
            print(message)
            print("1 error, 0 warnings")
        return 1

    names, problems = check_repo(root)
    errors = sum(1 for problem in problems if problem.level == "error")
    warnings = sum(1 for problem in problems if problem.level == "warning")
    if args.as_json:
        print(json.dumps({
            "ok": errors == 0,
            "skills": names,
            "error_count": errors,
            "warning_count": warnings,
            "problems": [asdict(problem) for problem in problems],
        }, indent=2))
    else:
        for problem in problems:
            print(format_problem(problem))
        print("skills: %s" % (", ".join(names) if names else "(none)"))
        print("%d %s, %d %s" % (errors, "error" if errors == 1 else "errors",
                                warnings, "warning" if warnings == 1 else "warnings"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
