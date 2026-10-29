# This repository is the BeautifulRust fork for allspark

A fork of [morganlinton/BeautifulRust](https://github.com/morganlinton/BeautifulRust).
The upstream project teaches coding agents to write, review, and port idiomatic Rust with
three Agent Skills plus a tested clippy config.

The fork adds one thing: an adaptation layer in [`allspark/`](allspark/README.md) that
installs and validates the same skills in **allspark** (`/root/prime-agent`, the fork of
`PrimeIntellect-ai/prime-agent`) and syncs new upstream releases.

Nothing under `skills/` is rewritten. Both projects implement the Agent Skills standard, so
allspark's package resolver reads `skills/*/SKILL.md` directly. The fork keeps upstream
files untouched, which is what makes an upstream release a clean merge.

## Remotes

```
origin    https://github.com/rager306/BeautifulRust        (fetch/push)
upstream  https://github.com/morganlinton/BeautifulRust.git (fetch; push disabled)
```

## Quick start

Install in allspark:

```bash
allspark package install https://github.com/rager306/BeautifulRust
```

Check and sync:

```bash
python3 allspark/check.py
python3 allspark/sync.py status --fetch
python3 allspark/sync.py check --full
python3 allspark/sync.py sync --apply --full --tag
```

`sync` is a dry run without `--apply`. It merges `upstream/main`, runs the upstream
validator, the allspark checker, the unit tests, the example compiler, and the lint proof,
then records the upstream commit in `allspark/upstream.json`. A failed check aborts the
merge. Read [`allspark/README.md`](allspark/README.md) for the compatibility contract, the
automation workflow, and the limits of auto-adaptation.
