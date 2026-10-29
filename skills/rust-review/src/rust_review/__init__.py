"""Inventory a Rust tree for the rust-review Agent Skill.

This package is installed into allspark's Python kernel. It gathers the
mechanical evidence a review starts with: files, public API surface, panic
sites, unsafe blocks, clones, and test markers. It does not judge the code and
does not write the review; the agent reads the source and does that.

Call it from the kernel:

    print(await rust_review("src", include_tests=False))
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Tuple

SKIP_DIRS = {".git", ".cargo", "target", "vendor", "node_modules"}
DEFAULT_MAX_FILES = 200
CAP_PER_CATEGORY = 8

PUBLIC_ITEM = re.compile(
    r"^\s*pub(?:\s*\([^)]*\))?\s+(?:async\s+)?(?:unsafe\s+)?"
    r"(?P<kind>fn|struct|enum|trait|type|const|static|mod)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
UNWRAP = re.compile(r"\.unwrap\s*\(")
EXPECT = re.compile(r"\.expect\s*\(")
PANIC = re.compile(r"\b(?:panic|todo|unimplemented|unreachable)!\s*\(")
UNSAFE_BLOCK = re.compile(r"\bunsafe\s*\{")
UNSAFE_FN = re.compile(r"\bunsafe\s+fn\b")
SAFETY_COMMENT = re.compile(r"//\s*SAFETY:")
TEST_ATTR = re.compile(r"#\s*\[\s*(?:test|cfg\s*\(\s*test\s*\))\s*\]")
CLONE = re.compile(r"\.clone\s*\(")
RC_REFCELL = re.compile(r"\b(?:Rc|Arc)\s*<\s*RefCell\s*<")


@dataclass
class FileReport:
    path: str
    line_count: int
    public: List[Tuple[int, str, str]] = field(default_factory=list)
    unwrap: List[int] = field(default_factory=list)
    expect: List[int] = field(default_factory=list)
    panic: List[int] = field(default_factory=list)
    unsafe_blocks: List[int] = field(default_factory=list)
    unsafe_fns: List[int] = field(default_factory=list)
    safety_comments: int = 0
    tests: List[int] = field(default_factory=list)
    clones: List[int] = field(default_factory=list)
    rc_refcell: List[int] = field(default_factory=list)


def _display(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _rust_files(root: Path, include_tests: bool) -> List[Path]:
    if root.is_file():
        return [root] if root.suffix == ".rs" else []
    files: List[Path] = []
    for path in root.rglob("*.rs"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not include_tests and (
            "tests" in path.parts or path.name == "tests.rs" or path.stem.endswith("_test")
        ):
            continue
        files.append(path)
    return sorted(files)


def _scan(path: Path, display: str) -> FileReport:
    text = path.read_text(encoding="utf-8", errors="replace")
    report = FileReport(path=display, line_count=len(text.splitlines()))
    for number, line in enumerate(text.splitlines(), 1):
        match = PUBLIC_ITEM.match(line)
        if match:
            report.public.append((number, match.group("kind"), match.group("name")))
        if UNWRAP.search(line):
            report.unwrap.append(number)
        if EXPECT.search(line):
            report.expect.append(number)
        if PANIC.search(line):
            report.panic.append(number)
        if UNSAFE_BLOCK.search(line):
            report.unsafe_blocks.append(number)
        if UNSAFE_FN.search(line):
            report.unsafe_fns.append(number)
        report.safety_comments += len(SAFETY_COMMENT.findall(line))
        if TEST_ATTR.search(line):
            report.tests.append(number)
        if CLONE.search(line):
            report.clones.append(number)
        if RC_REFCELL.search(line):
            report.rc_refcell.append(number)
    return report


def _positions(values: Iterable[int], cap: int = CAP_PER_CATEGORY) -> str:
    values = list(values)
    if not values:
        return "none"
    shown = [":%d" % value for value in values[:cap]]
    if len(values) > cap:
        shown.append("(+%d more)" % (len(values) - cap))
    return ", ".join(shown)


def _file_section(report: FileReport) -> str:
    lines = ["## `%s` — %d lines" % (report.path, report.line_count)]
    if report.public:
        items = [
            "`%s %s`:%d" % (kind, name, number)
            for number, kind, name in report.public[:CAP_PER_CATEGORY]
        ]
        if len(report.public) > CAP_PER_CATEGORY:
            items.append("(+%d more)" % (len(report.public) - CAP_PER_CATEGORY))
        lines.append("- public API: " + ", ".join(items))
    if report.unwrap:
        lines.append("- `unwrap()`: " + _positions(report.unwrap))
    if report.expect:
        lines.append("- `expect()`: " + _positions(report.expect))
    if report.panic:
        lines.append("- `panic!`/`todo!`/`unimplemented!`/`unreachable!`: " + _positions(report.panic))
    if report.unsafe_blocks or report.unsafe_fns or report.safety_comments:
        lines.append(
            "- unsafe blocks: %s; `unsafe fn`: %s; `// SAFETY:` comments: %d"
            % (
                _positions(report.unsafe_blocks),
                _positions(report.unsafe_fns),
                report.safety_comments,
            )
        )
    if report.tests:
        lines.append("- test markers: " + _positions(report.tests))
    if report.clones:
        lines.append("- `.clone()`: " + _positions(report.clones))
    if report.rc_refcell:
        lines.append("- `Rc`/`Arc` around `RefCell`: " + _positions(report.rc_refcell))
    return "\n".join(lines)


def _summary(reports: List[FileReport], total_files: int, scanned_files: int) -> str:
    def total(attribute: str) -> int:
        return sum(len(getattr(report, attribute)) for report in reports)

    lines = [
        "## Summary",
        "- Rust files: %d (scanned %d)" % (total_files, scanned_files),
        "- lines: %d" % sum(report.line_count for report in reports),
        "- public API items: %d" % total("public"),
        "- `unwrap()`: %d" % total("unwrap"),
        "- `expect()`: %d" % total("expect"),
        "- `panic!`/`todo!`/`unimplemented!`/`unreachable!`: %d" % total("panic"),
        "- unsafe blocks: %d; `unsafe fn`: %d; `// SAFETY:` comments: %d"
        % (
            total("unsafe_blocks"),
            total("unsafe_fns"),
            sum(report.safety_comments for report in reports),
        ),
        "- test markers: %d" % total("tests"),
        "- `.clone()`: %d" % total("clones"),
        "- `Rc`/`Arc` around `RefCell`: %d" % total("rc_refcell"),
    ]
    return "\n".join(lines)


async def run(
    path: str = ".",
    include_tests: bool = False,
    max_files: int = DEFAULT_MAX_FILES,
) -> str:
    """Summarize a Rust tree for review: files, public API, panics, unsafe, tests, clones.

    Args:
        path: A Rust file or a directory to scan.
        include_tests: Include files under tests/ and *_test.rs.
        max_files: Maximum number of Rust files to read. The rest are counted but not read.

    Returns:
        A markdown inventory with file:line references. It is evidence, not a verdict.
    """
    root = Path(path).expanduser()
    if not root.exists():
        return "# Rust review inventory\n\npath does not exist: `%s`\n" % path

    limit = max(1, max_files)
    files = _rust_files(root, include_tests)
    if not files:
        return "# Rust review inventory\n\nno Rust files found under `%s`\n" % path

    base = root if root.is_dir() else root.parent
    selected = files[:limit]
    reports = [_scan(file, _display(file, base)) for file in selected]

    header = [
        "# Rust review inventory",
        "",
        "root: `%s` — %d Rust files%s"
        % (
            path,
            len(files),
            "" if include_tests else " (test files skipped)",
        ),
        "",
    ]
    body = [_file_section(report) for report in reports]
    if len(files) > limit:
        body.append("## Truncated\n- %d more files were not read (raise `max_files`)." % (len(files) - limit))
    return "\n".join(header + body + ["", _summary(reports, len(files), len(selected))]) + "\n"


__all__ = ["run"]
