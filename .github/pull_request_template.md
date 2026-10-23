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
- [ ] `make test` before marking ready

New behaviour is covered by a new test / the fix has a test that fails without it:

<!-- name the test(s) -->

## Stack hygiene

- [ ] exactly one commit (`git rev-list --count origin/integration/3.0..HEAD` → `1`)
- [ ] Conventional Commits subject, rebased on current `integration/3.0`
- [ ] no fork-only paths in the diff (`git diff --name-only origin/integration/3.0...HEAD`)
- [ ] applies cleanly onto pure `3.0` — this commit is contributable as-is

## Upstream readiness

- [ ] Ready to mirror to `Emerge-Lab/PufferDrive` now
- [ ] Fork-internal for now, because: <!-- reason -->
- [ ] Never upstream (`chore(fork):`)

## Risk / notes

<!-- Behaviour changes, config or observation-layout changes, perf impact, follow-ups. -->
