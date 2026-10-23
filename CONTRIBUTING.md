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

A second permanent draft PR, **`fork-only/3.0` → `3.0`**, shows the fork-only content on its own,
with no features mixed in — the quickest answer to "what does this fork add that never goes
upstream?". Its branch is rebuilt automatically from the `chore(fork):` commit on every push to
`integration/3.0`, so it never drifts. **Never merge it and never commit to it.** Like the PR above
it carries `do-not-merge` **alone**: the two labels *together* are reserved for a PR that is
actively rewriting the process, and a permanent PR wearing both would signal that forever.

When upstream advances, a bot opens a `mirror-sync` issue asking a maintainer to rebase the stack.

## Working on a feature

```bash
git fetch origin && git fetch upstream
git switch -c feature/<slug> origin/integration/3.0
# ... work ...
pre-commit run --all-files
make rebuild        # if you touched .c/.h
make test           # unit + eval + c + notebooks (NOT docker-smoke or perf — run those separately)

# squash to exactly ONE commit, without needing an editor
git rebase origin/integration/3.0
git reset --soft origin/integration/3.0
git commit -m "feat(scope): subject"    # use -m or -F; a bare `git commit` needs an editor
git rev-list --count origin/integration/3.0..HEAD    # must print 1
```

If the rebase conflicts: resolve, `git add`, then `GIT_EDITOR=true git rebase --continue`
(plain `--continue` wants an editor). `git rebase --abort` backs out.

Then open a PR against `integration/3.0` and fill in the template.

Keep each PR to one idea. Conventional Commits subjects (`feat(drive): …`, `fix(reward): …`).
A human merges with **Rebase and merge** so the stack stays linear.

First time in a clone? `upstream` is not set up for you:

```bash
git remote add upstream https://github.com/Emerge-Lab/PufferDrive.git
gh repo set-default ASCS-eV/PufferDrive
```

## Before anything is stacked

Opening the PR is not the finish line. `integration/3.0` is what BMW pins *and* the base every
later feature rebases onto, so a defect merged there is multiplied. Three gates, all required:

1. **Green CI** on the final commit — not "green except a flake", not "green before the last push".
2. **Adversarial review** by someone other than the author, who tries to *break* the change: revert
   the fix and prove the new test fails, hunt edge cases, check determinism, check observation and
   config layout, check hot-path cost, check scope creep. "LGTM" is not a review. The findings go in
   the PR thread, including the probes that found nothing.
3. **Proven upstream-readiness** — the commit is cherry-picked onto a branch cut from pure
   `upstream/3.0` *before* merging. It must apply without conflict and produce an upstream-clean
   diff. We stack only commits we could contribute today. (`chore(fork):` commits are exempt from
   this one — they can never be upstream-ready.)

Actually opening the upstream PR is a separate step that needs human approval. Gate 3 only proves
we *could*.

A correct feature that is not yet upstream-ready stays an open PR. That is a smaller problem than a
stack nobody can contribute from.

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
| before marking ready | `make test` (unit + eval + c + notebooks only) |

`make test` does **not** run `test-docker-smoke` or the perf target — run those explicitly when the
table says so. If your change moves the smoke goldens, regenerate them with
`tests/smoke_tests/update_goldens.sh` and stage both JSON files in the same commit (the rollout
golden is gitignored and needs `git add -f`).

New behaviour needs a new test; a bug fix needs a test that fails without the fix. List the commands
you ran in the PR.

## Fork-only content

Two kinds, checked differently.

**Files that exist only here** — `CONTRIBUTING.md`, `.github/copilot-instructions.md`,
`.github/pull_request_template.md`, `.github/workflows/sync-mirror.yml`,
`.github/workflows/fork-only-view.yml`. None may appear in a feature diff.

**Fork-only hunks inside shared files** — the `fork:begin`/`fork:end` block in `AGENTS.md` and
`CLAUDE.md`, and the `integration/3.0` entry in the `pull_request` branch filter of
`.github/workflows/ci.yml` and `.github/workflows/install.yml`. Here the *file* may legitimately
appear in a feature diff; only those hunks must stay untouched.

```bash
git rev-list --count origin/integration/3.0..HEAD       # exactly 1
git diff --name-only origin/integration/3.0...HEAD      # no fork-only file
git diff origin/integration/3.0...HEAD -- AGENTS.md CLAUDE.md \
    .github/workflows/ci.yml .github/workflows/install.yml   # no fork-only hunk
```

## Changing the fork's own files

Fork-only content is kept as **exactly one** `chore(fork):` commit at the base of the stack, so that
every other commit on the stack is obviously contributable. To change it:

1. Branch `chore/<slug>` off `integration/3.0`, one commit.
2. Open it as a **draft** PR titled `[do-not-mirror][do-not-merge] chore(fork): …`, carrying **both**
   labels, and tick "Never upstream" in the template. Two independent axes: `do-not-mirror` means it
   must never reach upstream, `do-not-merge` means the merge button is never pressed on it — merging
   would leave a second `chore(fork):` commit and abort every future sync.
3. Green CI and an adversarial review are still required. Upstream-readiness is not — it cannot be.
4. It is then **folded into the base commit by fixup and force-pushed**, not merged with the button
   (which would leave two fork commits). The PR ends up closed rather than merged; post the
   resulting SHA in the thread.

Full recipe in [`.github/copilot-instructions.md`](.github/copilot-instructions.md) section 3.3.

## Contributing back upstream

When we decide a feature is ready for `Emerge-Lab/PufferDrive`, a maintainer cherry-picks its single
commit onto a branch cut from the pure `3.0` mirror and opens the PR there. Coding agents may
prepare that branch and may open the PR, but **only after a human has verified the final title,
body and diff** — the agent may press the button, it does not get to decide the content is fit to
publish.

Upstream's own `AGENTS.md` guidelines apply to anything we send them, with one declared exception:
their `AGENTIC_PR` poem-file instruction conflicts with keeping the mirror diff clean, so we
**disclose AI assistance in the PR body and say that we made that choice** rather than silently
skipping it. See copilot-instructions.md section 6.1 — silently doing neither is not an option.

## Code style

Upstream's [`AGENTS.md`](AGENTS.md) is authoritative for C/Python style, trust boundaries,
determinism, and performance rules. Follow it; we do not maintain a competing style guide.

## Please don't

- merge a feature PR before it has green CI, an adversarial review, and a verified `mirror/<slug>`
  cherry-pick onto pure upstream
- commit to `3.0` — it is auto-synced, blocks force-pushes and deletions, and any direct commit
  trips the sync workflow's fast-forward check
- merge the standing `integration/3.0 → 3.0` draft PR
- merge the standing `fork-only/3.0 → 3.0` draft PR, or commit to that branch — it is force-pushed
  from the `chore(fork):` commit and anything committed to it is silently discarded
- create merge commits on `integration/3.0`
- branch a feature off another unmerged feature
- open a PR that targets `3.0` (the two standing draft PRs are the only ones that ever may)
- run the `update-smoke-golden` workflow against `3.0` — it pushes commits to the branch it runs on
- commit secrets, internal identifiers, internal URLs, or proprietary scenario data — this fork is
  public
