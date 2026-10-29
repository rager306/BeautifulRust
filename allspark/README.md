# allspark adaptation

This directory is fork-only. It adapts [BeautifulRust](https://github.com/morganlinton/BeautifulRust)
to **allspark** (`/root/prime-agent`, the fork of `PrimeIntellect-ai/prime-agent`) and
automates the part that can be automated when upstream publishes a new version.

Nothing here is read by Claude Code, by allspark at runtime, or by an Agent Skills loader.
The loader reads `skills/*/SKILL.md`; this directory holds the compatibility check, the
upstream sync tool, and the record of the last synced upstream commit.

## Verdict

The **skills layer already works in allspark**. Both projects implement the
[Agent Skills standard](https://agentskills.io/specification): a `skills/<name>/SKILL.md`
directory with `name` and `description` frontmatter. allspark discovers a capability
package's `skills/` directory by convention, so the three upstream skills load without a
content rewrite.

Tested on 2026-10-29 with allspark `0.10.2-beta.10` (fork build `fork.012`):

```bash
allspark package install https://github.com/rager306/BeautifulRust
```

The clone lands in `$ALLSPARK_HOME/.prime/agent/git/github.com/rager306/BeautifulRust`
and the package resolver finds `skills/rust-best-practices`, `skills/rust-review`, and
`skills/rust-port`. `allspark package list` reports the package; `allspark package update`
updates it from the fork.

What does **not** carry over, and does not need to:

| Upstream piece | In allspark |
| --- | --- |
| `.claude-plugin/plugin.json`, `marketplace.json` | Ignored. allspark has capability packages, not Claude plugins. |
| `claude plugin install`, `npx skills add`, Gemini/Copilot commands | Replaced by `allspark package install`. |
| `claude plugin eval` cases | Development-only. They are not loaded at runtime. |
| Cross-agent benchmark | Documentation. No runtime effect. |
| `lints/` clippy config | Independent of the agent; use it in any Cargo project. |

The only runtime surface is `skills/*/SKILL.md` plus the files under each skill's
`references/` and `assets/`. Everything the checker reads is exactly that surface.

`rust-review` is also a **Python-backed skill**: `skills/rust-review/pyproject.toml` and
`skills/rust-review/src/rust_review/__init__.py` make allspark install the package into
the kernel and expose `rust_review`. The markdown skill stays the instruction source;
the Python call is an extra, machine-checkable step.

## Install for allspark

User scope (the usual choice):

```bash
allspark package install https://github.com/rager306/BeautifulRust
```

Project scope, so the package is recorded in the project rather than the user settings:

```bash
allspark package install --local https://github.com/rager306/BeautifulRust
```

Update, or pin a release tag:

```bash
allspark package update
allspark package install https://github.com/rager306/BeautifulRust#allspark-v1.4.0-fork.001
```

Remove:

```bash
allspark package remove https://github.com/rager306/BeautifulRust
```

Without the package manager, copy the three skill folders into the directory allspark
actually scans for user skills, `$PRIME_AGENT_CODING_AGENT_DIR/skills` (the launcher sets
that to `$ALLSPARK_HOME/.prime/agent/skills`), or into a project's
`.prime/agent/skills/`. The skill-creator document that mentions `~/.allspark/skills` does
not match the `0.10.2` loader; the package install path above is the supported one.

## Compatibility contract

`allspark/check.py` enforces the rules allspark applies on top of the upstream validator:

- every discovered skill has a `name` matching its folder, at most 64 characters, using
  lowercase `a-z`, `0-9`, and single hyphens;
- `description` is present, non-empty, and at most 1024 characters, because a skill with
  no description is silently skipped;
- `disable-model-invocation` is not `true`, which would hide the skill from the prompt;
- `SKILL.md` is below allspark's 256 KiB context-file cap;
- loaded content contains no Claude-only construct (`${CLAUDE_PLUGIN_ROOT}`,
  `.claude-plugin`, `claude plugin`, `/plugin install`, `claude --plugin-dir`);
- a Python-backed skill (`pyproject.toml`) satisfies allspark's contract:
  `src/<name_with_underscores>/__init__.py` exists, the CLI script name matches the import
  name, the hatch wheel lists `src/<name_with_underscores>`, and
  `prime-agent-runtime` is not a dependency;
- `package.json`, if upstream adds one, does not override `pi.skills` away from `skills/`;
- the three core skills (`rust-best-practices`, `rust-review`, `rust-port`) are present;
- discovery matches allspark's stopping rule: a directory with `SKILL.md` is a skill root,
  so `evals/` is never scanned as a skill.

Run it directly:

```bash
python3 allspark/check.py
python3 allspark/check.py --json
```

Exit code 1 means a release needs a deliberate adaptation before the fork tag.

## Python-backed rust-review

allspark detects a Python skill by `pyproject.toml` at the skill root and
`src/<name_with_underscores>/__init__.py`. `rust-review` meets that contract, so the kernel
exposes `rust_review`:

```python
print(await rust_review("src", include_tests=False))
```

The call returns a markdown inventory: files, public API items, `unwrap()`/`expect()`,
`panic!`, unsafe blocks, `// SAFETY:` comments, test markers, clones, and
`Rc`/`Arc<RefCell<..>>`, each with `file:line` references. It is evidence, not a verdict;
the agent still reads the code and writes the prioritized report. `SKILL.md` documents the
call under "Run the tools first".

The package has no third-party dependencies. `uv pip install --editable` with hatchling
build isolation was tested in a clean venv, and the upstream validators do not reject the
extra files.

## Sync with upstream

`allspark/sync.py` wraps the release procedure. `sync` is a dry run unless `--apply` is
given:

```bash
python3 allspark/sync.py status --fetch
python3 allspark/sync.py check --full
python3 allspark/sync.py sync --apply --full --branch allspark/sync --tag
```

`sync --apply` does exactly this:

1. refuse to start on a dirty working tree;
2. `git fetch upstream --tags --prune`;
3. `git merge --no-commit --no-ff upstream/main` on the current branch (or `--branch`);
4. run `scripts/check-skill.py` and `allspark/check.py`, and with `--full` the unit
   tests, `scripts/check-examples.py`, and `scripts/check-lints.sh`;
5. abort the merge and exit 1 if a check fails; abort and exit 2 on a conflict;
6. write `allspark/upstream.json` with the upstream commit, describe string, plugin
   version, and the checks that passed;
7. commit `chore(allspark): sync upstream <describe> (<sha>)`;
8. optionally tag `allspark-v<plugin-version>-fork.NNN` and push to `origin`.

The fork never pushes to `upstream`; its push URL is `no_push`.

## What is automatic, and what is not

| Upstream change | Automatic? | Why |
| --- | --- | --- |
| Text, examples, references inside existing skills | **Yes** | Merge is clean; the validators cover format and links. |
| A new skill folder under `skills/` | **Yes** | Convention discovery picks it up; the checker validates it; no manifest lists skills. |
| A new upstream version/tag | **Yes** | `sync` records it and creates a fork tag. |
| Upstream edits `.github/`, `README.md`, benchmarks | **Yes** | The fork does not modify those files, so the merge is clean. |
| A core skill is renamed or removed | **No** | The checker fails on the missing core skill; a human decides the replacement. |
| Frontmatter rules change, or a new required field appears | **No** | The checker fails; update `allspark/check.py`. |
| A Claude-only construct enters a loaded skill | **No** | The checker fails; either rewrite the text or record why it is safe. |
| A `pyproject.toml` turns a skill into a Python skill | **Yes, with validation** | The checker verifies the package layout, script name, wheel entry, and forbidden runtime dependency. |
| The repo moves `skills/` or changes its layout | **No** | Discovery finds no skills; the checker fails. |
| License or source-attribution changes | **No** | The checker does not judge policy; review the diff. |
| A merge conflict in a file the fork also edits | **No** | `sync` aborts and lists the paths. The fork edits no upstream skill or documentation file; the only upstream edit is three lines in `.gitignore` for Python caches. |

In short: **auto-adaptation is realistic for the normal release path** (content edits, new
skills, version bumps) because the adapter is a validator, not a transformer. It becomes
semi-automatic when upstream changes the format: the pipeline stops, and the failure names
the exact rule to update. No model-based rewriting is needed today, and adding one would
create a second source of truth for skill text.

## GitHub Actions

`.github/workflows/allspark-check.yml` runs on every push and pull request that touches
`allspark/`, `skills/`, `tests/`, or `scripts/check-skill.py`. It runs the unit tests, the
allspark checker, the upstream validator, and an editable install/import smoke test for
the Python skill. This is the job that catches an adapter regression before the next
upstream release.

`.github/workflows/allspark-sync.yml` runs the same sync script daily and on demand. It
creates `allspark/sync` and opens a pull request instead of pushing to `main`; a green PR
is the review gate. The workflow serializes runs with a concurrency group, has a 30-minute
timeout, caches the Cargo registry, writes the PR body from the actual diff, and opens or
comments on an issue when a run fails. Merge the PR with a merge commit, not a squash: the
fork tag points at the branch commit. If a sync PR is rejected, delete its
`allspark-v*-fork.*` tag; it was created on the branch before review. GitHub disables
workflows on forks by default, so enable Actions in the fork's settings once.

The workflow uses `github.token` by default. If the repo does not allow Actions to create
pull requests (Settings > Actions > General), add a fine-grained `ALLSPARK_SYNC_TOKEN`
secret with `contents: write`, `pull-requests: write`, and `issues: write`; otherwise the
branch is still pushed and the job prints a compare link instead of failing.

## Python skill tests

`tests/test_rust_review.py` covers the `rust_review` inventory: file selection,
`include_tests`, `max_files`, a single file, and a missing path. The check workflow also
installs the package editable into a clean venv and imports it, so a broken
`pyproject.toml` or `src/` layout fails CI.

## Layout

```
allspark/
├── README.md         this file
├── check.py          allspark compatibility validator
├── sync.py           fetch → merge → validate → tag
└── upstream.json     last synced upstream commit and check result

skills/rust-review/
├── pyproject.toml    Python-backed skill manifest
└── src/rust_review/__init__.py  the rust_review() inventory call
```
