"""Inventory a Rust tree for the rust-review Agent Skill.

This package is installed into allspark's Python kernel. It gathers the
mechanical evidence a review starts with: files, public API surface, panic
sites, unsafe blocks, clones, and test markers. It does not judge the code and
does not write the review; the agent reads the source and does that.

Inline `#[test]` functions and `#[cfg(test)]` modules are excluded from the
default counts. Use `include_inline_tests=True` to include them.

Call it from the kernel:

    print(await rust_review("src", include_tests=False))
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

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
TEST_ATTR = re.compile(
    r"#\s*\[\s*(?:test|tokio::test|async_std::test|rstest|cfg\s*\(\s*test\s*\))"
)
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
    test_only: Dict[str, int] = field(default_factory=dict)


def _display(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _is_test_file(path: Path) -> bool:
    return "tests" in path.parts or path.name == "tests.rs" or path.stem.endswith("_test")


def _rust_files(root: Path, include_tests: bool) -> List[Path]:
    if root.is_file():
        return [root] if root.suffix == ".rs" else []
    files: List[Path] = []
    for path in root.rglob("*.rs"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not include_tests and _is_test_file(path):
            continue
        files.append(path)
    return sorted(files)


def _is_test_attribute(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("#["):
        return False
    if re.match(r"^#\[\s*(?:test|tokio::test|async_std::test|rstest)\b", stripped):
        return True
    if re.match(r"^#\[\s*cfg\s*\(", stripped) and re.search(r"\btest\b", stripped):
        return True
    return False


def _brace_end(lines: List[str], start_line: int, start_col: int) -> int:
    depth = 0
    in_line_comment = False
    block_depth = 0
    quote = ""
    raw_hashes = None
    escape = False
    for line_index in range(start_line, len(lines)):
        line = lines[line_index]
        col = start_col if line_index == start_line else 0
        while col < len(line):
            char = line[col]
            nxt = line[col + 1] if col + 1 < len(line) else ""
            if in_line_comment:
                break
            if block_depth:
                if char == "/" and nxt == "*":
                    block_depth += 1
                    col += 2
                    continue
                if char == "*" and nxt == "/":
                    block_depth -= 1
                    col += 2
                    continue
                col += 1
                continue
            if raw_hashes is not None:
                if char == '"' and line[col + 1 : col + 1 + raw_hashes] == "#" * raw_hashes:
                    col += 1 + raw_hashes
                    raw_hashes = None
                    continue
                col += 1
                continue
            if quote:
                if escape:
                    escape = False
                    col += 1
                    continue
                if char == "\\":
                    escape = True
                    col += 1
                    continue
                if char == quote:
                    quote = ""
                col += 1
                continue
            if char == "/" and nxt == "/":
                in_line_comment = True
                break
            if char == "/" and nxt == "*":
                block_depth = 1
                col += 2
                continue
            if char == "r" or (char == "b" and nxt == "r"):
                prefix = 2 if char == "b" else 1
                index = col + prefix
                hashes = 0
                while index < len(line) and line[index] == "#":
                    hashes += 1
                    index += 1
                if index < len(line) and line[index] == '"':
                    raw_hashes = hashes
                    col = index + 1
                    continue
            if char in ('"', "'"):
                if char == "'":
                    if col + 2 < len(line) and line[col + 2] == "'":
                        quote = "'"
                        col += 1
                        continue
                    if col + 1 < len(line) and line[col + 1] == "\\":
                        quote = "'"
                        col += 1
                        continue
                    col += 1
                    continue
                quote = char
                col += 1
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return line_index
            col += 1
        in_line_comment = False
    return len(lines) - 1


def _test_ranges(lines: List[str]) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    index = 0
    while index < len(lines):
        if not _is_test_attribute(lines[index]):
            index += 1
            continue
        found = None
        for candidate in range(index, min(len(lines), index + 20)):
            brace = lines[candidate].find("{")
            if brace != -1:
                semicolon = lines[candidate].find(";")
                if semicolon == -1 or semicolon > brace:
                    found = (candidate, brace)
                break
            if ";" in lines[candidate]:
                break
        if found is None:
            index += 1
            continue
        end = _brace_end(lines, found[0], found[1])
        ranges.append((index + 1, end + 1))
        index = end + 1
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _scan(path: Path, display: str, include_tests: bool, include_inline_tests: bool) -> FileReport:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    report = FileReport(path=display, line_count=len(lines))
    ranges = (
        []
        if include_inline_tests or (_is_test_file(path) and include_tests)
        else _test_ranges(lines)
    )

    def record(name: str, values: List[int], number: int) -> None:
        if any(start <= number <= end for start, end in ranges):
            report.test_only[name] = report.test_only.get(name, 0) + 1
        else:
            values.append(number)

    for number, line in enumerate(lines, 1):
        match = PUBLIC_ITEM.match(line)
        if match and not any(start <= number <= end for start, end in ranges):
            report.public.append((number, match.group("kind"), match.group("name")))
        if UNWRAP.search(line):
            record("unwrap", report.unwrap, number)
        if EXPECT.search(line):
            record("expect", report.expect, number)
        if PANIC.search(line):
            record("panic", report.panic, number)
        if UNSAFE_BLOCK.search(line):
            record("unsafe_blocks", report.unsafe_blocks, number)
        if UNSAFE_FN.search(line):
            record("unsafe_fns", report.unsafe_fns, number)
        if not any(start <= number <= end for start, end in ranges):
            report.safety_comments += len(SAFETY_COMMENT.findall(line))
        if TEST_ATTR.search(line):
            record("test_markers", report.tests, number)
        if CLONE.search(line):
            record("clone", report.clones, number)
        if RC_REFCELL.search(line):
            record("rc_refcell", report.rc_refcell, number)
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
    if report.test_only:
        excluded = ", ".join(
            "%s %d" % (name, count) for name, count in sorted(report.test_only.items())
        )
        lines.append("- inline tests excluded: " + excluded)
    return "\n".join(lines)


def _summary(
    reports: List[FileReport], total_files: int, scanned_files: int, include_inline_tests: bool
) -> str:
    def total(attribute: str) -> int:
        return sum(len(getattr(report, attribute)) for report in reports)

    test_only: Dict[str, int] = {}
    for report in reports:
        for name, count in report.test_only.items():
            test_only[name] = test_only.get(name, 0) + count

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
    if test_only and not include_inline_tests:
        lines.append(
            "- inline tests excluded: "
            + ", ".join("%s %d" % (name, count) for name, count in sorted(test_only.items()))
        )
    return "\n".join(lines)


async def run(
    path: str = ".",
    include_tests: bool = False,
    include_inline_tests: bool = False,
    max_files: int = DEFAULT_MAX_FILES,
) -> str:
    """Summarize a Rust tree for review: files, public API, panics, unsafe, tests, clones.

    Args:
        path: A Rust file or a directory to scan.
        include_tests: Include files under tests/ and *_test.rs.
        include_inline_tests: Include `#[test]` functions and `#[cfg(test)]` modules.
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
    reports = [
        _scan(file, _display(file, base), include_tests, include_inline_tests)
        for file in selected
    ]

    flags = []
    if not include_tests:
        flags.append("test files skipped")
    if include_inline_tests:
        flags.append("inline tests included")
    header = [
        "# Rust review inventory",
        "",
        "root: `%s` — %d Rust files%s"
        % (path, len(files), (" (" + "; ".join(flags) + ")") if flags else ""),
        "",
    ]
    body = [_file_section(report) for report in reports]
    if len(files) > limit:
        body.append("## Truncated\n- %d more files were not read (raise `max_files`)." % (len(files) - limit))
    return "\n".join(
        header
        + body
        + ["", _summary(reports, len(files), len(selected), include_inline_tests)]
    ) + "\n"


__all__ = ["run"]
