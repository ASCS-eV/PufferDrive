<!-- Fork PR into integration/3.0. Delete nothing; fill everything in. -->

## What

<!-- One or two sentences. If you need "and" to describe unrelated changes, split the PR. -->

## Why

<!-- Problem, or the capability we need. Link the issue if there is one. -->

## How tested

<!-- Commands you actually ran in this environment, and their outcome. -->

- [ ] `pre-commit run --all-files`
- [ ] `make rebuild` (required if `.c`/`.h` changed — zero new warnings)
- [ ] `make test-unit` / `make test-eval`
- [ ] `make test-c` (C sim logic)
- [ ] `make test-notebooks` (notebooks touched)
- [ ] `make test-docker-smoke` (dynamics, rewards, observations, or training loop touched)
- [ ] `make -C tests/drive perf` (hot paths touched)
- [ ] `make test` before marking ready (unit + eval + c + notebooks — does **not** include
      docker-smoke or perf; those are the two boxes above)

New behaviour is covered by a new test / the fix has a test that fails without it:

<!-- name the test(s) -->

## Stack hygiene

<!-- One command checks all four boxes:
       python .github/fork/forkctl.py check-commit
     On a chore/<slug> branch add --chore: the fork-only checks are exempt by design, the
     one-commit and subject checks still apply in full. Paste the output below. -->

- [ ] `forkctl check-commit` passes (`--chore` on a fork-only branch)
- [ ] Conventional Commits subject, rebased on current `integration/3.0`

<!-- paste the forkctl check-commit output -->

## Merge gate

<!-- All three must be green before this is stacked. See copilot-instructions.md §4.8. -->

- [ ] **Gate 1 — CI green** on the final commit (no "green except a flake")
- [ ] **Gate 2 — adversarial review** posted by someone other than the author; findings fixed or
      explicitly accepted in the thread
- [ ] **Gate 3 — upstream-readiness proven**: `forkctl mirror <slug> --sha HEAD` passes, i.e. the
      commit cherry-picks onto pure `upstream/3.0` and its diff is upstream-clean
      <!-- exempt for chore(fork): fork-only changes — tick the "Never upstream" box below -->

Gate 3 result:

<!-- paste the `forkctl mirror` output, or write "exempt: chore(fork)" -->

## Upstream disposition

- [ ] Mirror PR to `Emerge-Lab/PufferDrive` requested now — needs a human to verify the final
      title, body and diff first, and the body must carry the authorship disclosure from
      `.github/fork/PROCESS.md` → *Agent authorship*
- [ ] Contributable, but we hold it for now, because: <!-- reason -->
- [ ] **Never upstream** — fork-only `chore(fork):` change, Gate 3 exempt

<!--
Fork-only change? Two independent axes, BOTH must be marked:

  do-not-mirror (never reaches upstream) — all four:
    1. PR is a DRAFT
    2. title contains [do-not-mirror]
    3. the `do-not-mirror` label is applied
    4. the "Never upstream" box above is ticked

  do-not-merge (the merge button is never pressed) — both:
    5. title contains [do-not-merge]
    6. the `do-not-merge` label is applied

Merging it would put a SECOND chore(fork) commit on the stack. `forkctl check-stack` asserts there
is exactly one, so every future sync would abort. It is folded into the base chore(fork) commit by
fixup instead. See .github/fork/PROCESS.md -> Changing fork-only content.
-->


## Risk / notes

<!-- Behaviour changes, config or observation-layout changes, perf impact, follow-ups. -->
