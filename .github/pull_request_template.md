<!-- Fork PR into integration/3.0. Keep it short; the Fork gates check proves the rest. -->

## What

<!-- One or two sentences. If you need "and" for unrelated changes, split the PR. -->

## Why

<!-- The problem, or the capability we need. Link the issue if there is one. -->

## How tested

<!-- Only what CI cannot run for you. If CI covers it, write "CI" and stop.
     Name anything you ran locally that CI does not: docker-smoke, perf, a GPU run,
     a manual reproduction. A claim that a suite passed means it ran to completion. -->

New behaviour is covered by a new test / the fix has a test that fails without it:
<!-- name the test, or say why none applies -->

## Upstream disposition

- [ ] Mirror PR requested now — a human must verify title, body and diff first, and the body
      carries the disclosure from `.github/fork/PROCESS.md` → *Agent authorship*
- [ ] Contributable, held for now because: <!-- reason -->
- [ ] **Never upstream** — fork-only `chore(fork):` change

<!-- Fork-only? Then ALL of: draft PR, [do-not-mirror][do-not-merge] in the title, both labels,
     and the box above. Merging it would leave a second chore(fork) commit and abort every future
     sync; it gets folded in by fixup instead. See PROCESS.md -> Changing fork-only content. -->

## Risk / notes

<!-- Behaviour, config or observation-layout changes, perf impact, follow-ups. Delete if none. -->
