#!/usr/bin/env python3
"""Sync this fork with upstream and re-check allspark compatibility.

Commands:
  status [--fetch]   show where the fork stands relative to upstream
  check [--full]     run the upstream checks plus the allspark checker
  sync [--apply]     dry-run by default; --apply merges upstream/main

The sync is a merge, never a rebase. The fork only adds files under allspark/
(plus this workflow), so normal upstream releases merge cleanly. A new upstream
release is adapted in four steps: fetch, merge, validate, tag.

Exit codes for sync:
  0  up to date, dry-run, or a successful merge
  1  checks failed; the merge was aborted
  2  merge conflict; resolve it by hand
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:  # Python 3.6 and older
    pass

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_REMOTE = "upstream"
UPSTREAM_URL = "https://github.com/morganlinton/BeautifulRust.git"
META_PATH = ROOT / "allspark" / "upstream.json"
PLUGIN_MANIFEST = ROOT / ".claude-plugin" / "plugin.json"


def git(*args: str, capture: bool = True, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        check=check,
    )


def output(*args: str) -> str:
    result = git(*args)
    return result.stdout.strip() if result.returncode == 0 else ""


def configure_remotes() -> None:
    remotes = output("remote").split()
    if UPSTREAM_REMOTE not in remotes:
        git("remote", "add", UPSTREAM_REMOTE, UPSTREAM_URL)
        print("added remote %s -> %s" % (UPSTREAM_REMOTE, UPSTREAM_URL))
        return
    url = output("remote", "get-url", UPSTREAM_REMOTE)
    if url.rstrip("/") != UPSTREAM_URL.rstrip("/"):
        print("warning: remote %s points at %s, not %s" % (UPSTREAM_REMOTE, url, UPSTREAM_URL))
    push_url = output("remote", "get-url", "--push", UPSTREAM_REMOTE)
    if push_url and push_url != "no_push":
        print("warning: pushing to upstream is enabled; the sync script never does it")


def plugin_version() -> str:
    try:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        version = manifest.get("version")
        return str(version) if version else "unknown"
    except (OSError, json.JSONDecodeError):
        return "unknown"


def exact_tag(ref: str = "upstream/main") -> str:
    for tag in output("tag", "--points-at", ref).splitlines():
        if re.match(r"^v\d+\.\d+\.\d+", tag):
            return tag
    return ""


def read_meta() -> Dict[str, Any]:
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_meta(checks: Sequence[str]) -> None:
    meta = {
        "upstream_repo": UPSTREAM_URL,
        "upstream_commit": output("rev-parse", "upstream/main"),
        "upstream_describe": output("describe", "--tags", "--always", "upstream/main"),
        "upstream_tag": exact_tag("upstream/main"),
        "plugin_version": plugin_version(),
        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checks": list(checks),
    }
    META_PATH.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def upstream_counts() -> Optional[Tuple[int, int]]:
    if git("rev-parse", "--verify", "--quiet", "upstream/main").returncode != 0:
        return None
    counts = output("rev-list", "--left-right", "--count", "HEAD...upstream/main").split()
    if len(counts) != 2:
        return None
    return int(counts[0]), int(counts[1])


def require_clean_tree() -> bool:
    dirty = output("status", "--porcelain")
    if dirty:
        print("working tree is not clean; commit or stash these paths first:")
        for line in dirty.splitlines():
            print("  " + line)
        return False
    return True


def status(fetch: bool) -> int:
    configure_remotes()
    if fetch:
        print("fetching %s..." % UPSTREAM_REMOTE)
        result = git("fetch", UPSTREAM_REMOTE, "--tags", "--prune", capture=False)
        if result.returncode != 0:
            print("fetch failed")
            return 1
    counts = upstream_counts()
    if counts is None:
        print("upstream/main is not available; run: python3 allspark/sync.py status --fetch")
        return 1

    ahead, behind = counts
    branch = output("rev-parse", "--abbrev-ref", "HEAD")
    head = output("rev-parse", "--short", "HEAD")
    subject = output("log", "-1", "--format=%s", "HEAD")
    describe = output("describe", "--tags", "--always", "upstream/main")
    meta = read_meta()

    print("repo:      %s" % ROOT)
    print("branch:    %s" % branch)
    print("head:      %s %s" % (head, subject))
    print("upstream:  %s (%s)" % (describe, output("rev-parse", "--short", "upstream/main")))
    print("ahead:     %d" % ahead)
    print("behind:    %d" % behind)
    print("plugin:    %s" % plugin_version())
    if meta:
        print(
            "last sync: %s (%s)"
            % (meta.get("synced_at", "unknown"), str(meta.get("upstream_commit", ""))[:7])
        )
    else:
        print("last sync: never (run sync --apply --full)")
    if behind:
        print()
        print("next: python3 allspark/sync.py sync --apply --full")
    return 0


def run_checks(full: bool) -> List[str]:
    commands: List[Tuple[str, List[str]]] = [
        ("upstream validator", [sys.executable, "scripts/check-skill.py"]),
        ("allspark compatibility", [sys.executable, "allspark/check.py"]),
    ]
    if full:
        commands += [
            ("unit tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]),
            ("examples", [sys.executable, "scripts/check-examples.py"]),
            ("lints", ["bash", "scripts/check-lints.sh"]),
        ]

    passed: List[str] = []
    for label, command in commands:
        print("==> %s" % label)
        if label in ("examples", "lints") and shutil.which("cargo") is None:
            print("cargo is not on PATH")
            return passed
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(command, cwd=str(ROOT), env=environment)
        if result.returncode != 0:
            print("<== %s failed (exit %d)" % (label, result.returncode))
            return passed
        passed.append(label)
    print("all checks passed")
    return passed


def next_fork_tag(version: str) -> str:
    pattern = "allspark-v%s-fork.*" % version
    numbers: List[int] = []
    for tag in output("tag", "--list", pattern).splitlines():
        match = re.match(r"^allspark-v%s-fork\.(\d+)$" % re.escape(version), tag)
        if match:
            numbers.append(int(match.group(1)))
    return "allspark-v%s-fork.%03d" % (version, max(numbers, default=0) + 1)


def sync(apply: bool, full: bool, branch: Optional[str], tag: bool, push: bool) -> int:
    configure_remotes()
    print("fetching %s..." % UPSTREAM_REMOTE)
    result = git("fetch", UPSTREAM_REMOTE, "--tags", "--prune", capture=False)
    if result.returncode != 0:
        print("fetch failed")
        return 2

    counts = upstream_counts()
    if counts is None:
        print("upstream/main is not available")
        return 2
    ahead, behind = counts
    if behind == 0:
        print("up to date with upstream/main (ahead %d)" % ahead)
        return 0

    describe = output("describe", "--tags", "--always", "upstream/main")
    short = output("rev-parse", "--short", "upstream/main")
    print("upstream/main has %d new commit(s): %s (%s)" % (behind, describe, short))
    print("the fork is %d commit(s) ahead" % ahead)

    if not apply:
        print()
        print("dry run; nothing changed. Apply with:")
        print("  python3 allspark/sync.py sync --apply --full%s%s"
              % (" --tag" if tag else "", " --push" if push else ""))
        return 0

    if not require_clean_tree():
        return 1

    current = output("rev-parse", "--abbrev-ref", "HEAD")
    if branch and branch != current:
        exists = git("rev-parse", "--verify", "--quiet", "refs/heads/" + branch).returncode == 0
        if exists:
            git("switch", branch, check=True)
        else:
            git("switch", "-c", branch, check=True)
        print("switched to branch %s" % branch)

    print("merging %s..." % UPSTREAM_REMOTE + "/main")
    merge = git("merge", "--no-commit", "--no-ff", "upstream/main", capture=False)
    if merge.returncode != 0:
        conflicts = output("diff", "--name-only", "--diff-filter=U")
        git("merge", "--abort")
        print("merge conflict; aborted. Resolve these paths by hand:")
        for path in conflicts.splitlines():
            print("  " + path)
        return 2

    print("applying fork overlays...")
    overlay = subprocess.run(
        [sys.executable, "allspark/overlays.py", "apply"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    if overlay.returncode != 0:
        print(overlay.stdout.strip())
        print(overlay.stderr.strip())
        git("merge", "--abort")
        print("overlay failed; merge aborted")
        return 1
    if overlay.stdout.strip():
        print(overlay.stdout.strip())

    checks = run_checks(full)
    if len(checks) < (5 if full else 2):
        git("merge", "--abort")
        print("checks failed; merge aborted")
        return 1

    write_meta(checks)
    git("add", "-A")
    message = "chore(allspark): sync upstream %s (%s)" % (describe, short)
    commit = git("commit", "-m", message)
    if commit.returncode != 0:
        print(commit.stdout)
        print("commit failed; the merge is still staged")
        return 1
    print("committed: %s" % message)

    tag_name = ""
    if tag:
        version = plugin_version()
        tag_name = next_fork_tag(version)
        tag_message = "allspark adaptation of BeautifulRust %s (upstream %s)" % (version, describe)
        if git("tag", "-a", tag_name, "-m", tag_message).returncode != 0:
            print("tag failed: %s" % tag_name)
            return 1
        print("tagged: %s" % tag_name)

    if push:
        if git("push", "origin", "HEAD", capture=False).returncode != 0:
            print("push failed")
            return 1
        if tag_name and git("push", "origin", tag_name, capture=False).returncode != 0:
            print("tag push failed")
            return 1
        print("pushed to origin")

    print()
    print("done. inspect with: git show --stat HEAD")
    return 0


def check(full: bool) -> int:
    checks = run_checks(full)
    expected = 5 if full else 2
    return 0 if len(checks) == expected else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sync BeautifulRust with upstream and check allspark compatibility")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="show upstream position")
    status_parser.add_argument("--fetch", action="store_true", help="fetch upstream first")

    check_parser = subparsers.add_parser("check", help="run upstream and allspark checks")
    check_parser.add_argument("--full", action="store_true", help="also run tests, examples, and lints")

    sync_parser = subparsers.add_parser("sync", help="merge upstream and adapt")
    sync_parser.add_argument("--apply", action="store_true", help="change the working tree (default: dry run)")
    sync_parser.add_argument("--full", action="store_true", help="run tests, examples, and lints")
    sync_parser.add_argument("--branch", help="create or switch to this branch before merging")
    sync_parser.add_argument("--tag", action="store_true", help="tag the result allspark-v<version>-fork.NNN")
    sync_parser.add_argument("--push", action="store_true", help="push the branch and tag to origin")

    args = parser.parse_args(argv)
    if args.command == "status":
        return status(args.fetch)
    if args.command == "check":
        return check(args.full)
    return sync(args.apply, args.full, args.branch, args.tag, args.push)


if __name__ == "__main__":
    sys.exit(main())
