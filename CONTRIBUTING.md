# Contributing to ASCS-eV/PufferDrive

This is the ASCS e.V. fork of [`Emerge-Lab/PufferDrive`](https://github.com/Emerge-Lab/PufferDrive),
consumed as a git submodule by internal BMW projects.

The **normative, always-current process** lives in
[`.github/copilot-instructions.md`](.github/copilot-instructions.md) — it is read automatically by
coding agents, and it is the file to change when the process changes. This page is the short human
orientation; where the two disagree, that file wins.

## The short version

We track upstream's `3.0` line. `2.0` is frozen and has unrelated history — never mix the two.

```
upstream/3.0 ──► 3.0  (verbatim mirror, auto-synced, never commit here)
                  └─► integration/3.0  (DEFAULT branch; combined stack; what BMW pins)
                        └─► feature/<slug>  →  PR into integration/3.0
upstream/3.0 ──► mirror/<slug>  (cherry-pick of the same commit)  →  PR to upstream
```

`integration/3.0` is a **linear stack of single-commit features** on top of the upstream mirror,
plus one `chore(fork):` commit holding fork-only files. Every feature is written so it can be
lifted onto pure upstream `3.0` unchanged — that is what makes the mirror PR trivial.

A permanent **draft PR `integration/3.0` → `3.0`** (`DO NOT MERGE — integration stack CI`) keeps a
live CI verdict on all combined features. **Never merge it** — that would contaminate the mirror.

When upstream advances, a bot opens a `mirror-sync` issue asking a maintainer to rebase the stack.

## Working on a feature

```bash
git fetch origin && git fetch upstream
git switch -c feature/<slug> origin/integration/3.0
# ... work ...
pre-commit run --all-files
make rebuild        # if you touched .c/.h
make test           # full local suite
git rebase -i origin/integration/3.0    # squash to exactly ONE commit
```

Then open a PR against `integration/3.0` and fill in the template.

Keep each PR to one idea. Conventional Commits subjects (`feat(drive): …`, `fix(reward): …`).
Merge with **rebase and merge** so the stack stays linear.

## Testing expectations

| You changed | Run |
| --- | --- |
| anything | `pre-commit run --all-files` |
| `.c` / `.h` | `make rebuild` (zero warnings) |
| Python, env, eval | `make test-unit`, `make test-eval` |
| C sim logic | `make test-c` |
| notebooks | `make test-notebooks` |
| dynamics, rewards, observations, training loop | `make test-docker-smoke` |
| hot paths | `make -C tests/drive perf` |
| before marking ready | `make test` |

New behaviour needs a new test; a bug fix needs a test that fails without the fix. List the commands
you ran in the PR.

## Fork-only files

`CONTRIBUTING.md`, `.github/copilot-instructions.md`, `.github/pull_request_template.md`,
`.github/workflows/sync-mirror.yml`, the `fork:begin`/`fork:end` block in `AGENTS.md` and
`CLAUDE.md`, and the `integration/3.0` CI branch filters exist **only in this fork**. They live in a
single `chore(fork):` commit and must never appear in a feature commit. Before opening a PR:

```bash
git diff --name-only origin/integration/3.0...HEAD   # none of the above may appear
```

## Contributing back upstream

When we decide a feature is ready for `Emerge-Lab/PufferDrive`, a maintainer cherry-picks its single
commit onto a branch cut from the pure `3.0` mirror and opens the PR there. Coding agents may
prepare that branch but **must not open an upstream PR without explicit human approval**, and
upstream's own `AGENTS.md` guidelines apply to anything we send them.

## Code style

Upstream's [`AGENTS.md`](AGENTS.md) is authoritative for C/Python style, trust boundaries,
determinism, and performance rules. Follow it; we do not maintain a competing style guide.

## Please don't

- commit to `3.0` — it is auto-synced, blocks force-pushes and deletions, and any direct commit
  trips the sync workflow's fast-forward check
- merge the standing `integration/3.0 → 3.0` draft PR
- create merge commits on `integration/3.0`
- branch a feature off another unmerged feature
- commit secrets, internal identifiers, internal URLs, or proprietary scenario data — this fork is
  public
