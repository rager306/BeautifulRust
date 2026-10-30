#!/usr/bin/env python3
"""Fork-only overlays for upstream files.

Upstream owns README.md and skills/rust-review/SKILL.md. The fork adds a small
install note and a review policy that must survive upstream merges. The text
lives here; sync.py applies it after every merge, and check.py fails when a
marker is missing. If upstream changes an anchor, apply stops with the exact
path and anchor instead of silently dropping the fork policy.

    python3 allspark/overlays.py apply
    python3 allspark/overlays.py check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent

REVIEW_POLICY = """\
<!-- allspark:review-policy -->
## Allspark review policy

These rules override the generic guidance above when this skill runs in allspark.

- **Formatting drift is a report, not a fix.** `cargo fmt --check` tells you the tree is
  not clean. Do not run `cargo fmt` across the tree unless the user asked for a formatting
  change: a whole-tree reformat hides the review diff. Report the drift and leave the fix
  as a separate task.
- **Internal crates use simplicity mode.** If `Cargo.toml` has `publish = false`, or the
  crate is only used inside the workspace, treat it as application code even when it has a
  `lib.rs`: do not demand `missing_docs` on every item, `#[non_exhaustive]`, builders, or
  sealed traits. The project's ADR/architecture and its own callers are the contract.
- **Use the inventory as a map.** In allspark, `await rust_review("src")` gathers the
  mechanical inventory (files, public API, panics, unsafe, clones, test markers) with
  `file:line` references. Treat it as evidence, not a verdict; the report still needs the
  judgment and rule citations above.
- **Inventory counts are candidates, not bugs.** `rust_review` excludes inline `#[test]`
  and `#[cfg(test)]` code by default; pass `include_inline_tests=True` only when you want
  test coverage included. A `.clone()` count is a list of candidates, not redundant clones.
"""

FORK_NOTE = """\
<!-- allspark:fork-note -->
> **allspark fork.** This repository is the
> [allspark](https://github.com/rager306/allspark) fork of BeautifulRust. Install, the
> Python-backed `rust_review` skill, reload instructions, and upstream sync are documented
> in [FORK.md](FORK.md) and [allspark/README.md](allspark/README.md).
"""

PYTHON_CACHES = """\
# allspark:python-caches
__pycache__/
*.pyc
"""

OVERLAYS: List[Dict[str, str]] = [
    {
        "path": "skills/rust-review/SKILL.md",
        "marker": "<!-- allspark:review-policy -->",
        "anchor": "## 5. Offer the lints",
        "position": "before",
        "text": REVIEW_POLICY,
    },
    {
        "path": "README.md",
        "marker": "<!-- allspark:fork-note -->",
        "anchor": "# Beautiful Rust",
        "position": "after",
        "text": FORK_NOTE,
    },
    {
        "path": ".gitignore",
        "marker": "# allspark:python-caches",
        "anchor": "lints/check/Cargo.lock",
        "position": "after",
        "text": PYTHON_CACHES,
    },
]


class OverlayError(RuntimeError):
    pass


def render(base_text: str, overlay: Dict[str, str]) -> str:
    if overlay["marker"] in base_text:
        return base_text
    anchor = overlay["anchor"]
    if anchor not in base_text:
        raise OverlayError(
            "%s: anchor missing: %r; upstream changed the file, update allspark/overlays.py"
            % (overlay["path"], anchor)
        )
    block = overlay["text"].rstrip() + "\n\n"
    if overlay["position"] == "before":
        return base_text.replace(anchor, block + anchor, 1)
    return base_text.replace(anchor, anchor + "\n\n" + block.rstrip(), 1)


def _apply_one(root: Path, overlay: Dict[str, str]) -> Optional[str]:
    path = root / overlay["path"]
    if not path.is_file():
        raise OverlayError("%s: file is missing" % overlay["path"])
    text = path.read_text(encoding="utf-8")
    updated = render(text, overlay)
    if updated == text:
        return None
    path.write_text(updated, encoding="utf-8")
    return overlay["path"]


def apply(root: Path = ROOT) -> List[str]:
    changed: List[str] = []
    for overlay in OVERLAYS:
        applied = _apply_one(root, overlay)
        if applied:
            changed.append(applied)
    return changed


def check(root: Path = ROOT) -> List[str]:
    problems: List[str] = []
    for overlay in OVERLAYS:
        path = root / overlay["path"]
        if not path.is_file():
            problems.append("%s: file is missing" % overlay["path"])
            continue
        if overlay["marker"] not in path.read_text(encoding="utf-8"):
            problems.append(
                "%s: missing overlay marker %r; run python3 allspark/overlays.py apply"
                % (overlay["path"], overlay["marker"])
            )
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply fork-only overlays to upstream files")
    parser.add_argument("command", choices=["apply", "check"])
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        if args.command == "apply":
            changed = apply(root)
            if changed:
                for path in changed:
                    print("applied: %s" % path)
            else:
                print("overlays already applied")
            return 0
    except OverlayError as error:
        print("overlay error: %s" % error, file=sys.stderr)
        return 1
    problems = check(root)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print("overlays applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
