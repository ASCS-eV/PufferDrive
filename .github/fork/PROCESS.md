# ASCS-eV/PufferDrive — fork process reference

**Fork-only. Never cherry-pick this file upstream.**

This is the *detail*. The normative core lives in `.github/copilot-instructions.md`, which every
agent loads automatically; this file is deliberately **not** auto-loaded, because most sessions in
this repository touch the simulator and never touch the fork process.

Read the section you need, when you need it. The core tells you which one:

| You are about to... | Read |
| --- | --- |
| merge or close one of the two standing draft PRs | [Standing PRs](#the-standing-draft-prs) |
| change a fork-only file (`chore/<slug>`) | [Changing fork-only content](#changing-fork-only-content) |
| review someone else's feature PR | [Adversarial review](#adversarial-review) |
| open, refresh, or retire an upstream PR | [Mirror PR workflow](#mirror-pr-workflow-fork--upstream) |
| rebase the stack onto new upstream, or cut a release | [Syncing and releasing](#syncing-and-releasing) |

Anything not covered here is in the core. Where the two disagree, the core wins — but they should
not disagree, because the *mechanical* rules live in neither: they live in `forkctl.py`, which is
tested.

## The standing draft PRs

### The integration stack CI draft

A permanent **draft PR `integration/3.0` → `3.0`** titled `DO NOT MERGE — integration stack CI`,
carrying the `do-not-merge` label, gives us a continuous, always-visible CI verdict on *all combined
features at once*, which no individual feature PR can provide.

- **Never merge it. Never mark it ready for review.** Merging would push the fork's stack into the
  mirror and destroy the clean base that every upstream PR is cut from.
- It is force-pushed whenever the stack is rebased; its checks simply re-run. A red verdict means
  the combined stack is broken even if every feature PR was green — fix it before tagging a release.
- Close and reopen it only if it is accidentally merged or the branch is recreated.

### The fork-only content view draft

A second permanent **draft PR `fork-only/3.0` → `3.0`** titled `DO NOT MERGE — fork-only content
view`, carrying the `do-not-merge` label, answers
one question that nothing else answers cheaply: *what does this fork add that never goes upstream?*
Its **Files changed** tab is exactly the fork-only content, with no features mixed in — the standing
integration draft PR above cannot show this, because its diff necessarily contains the whole feature
stack as well.

- **It is a view, not storage.** Fork-only content lives in the single `chore(fork):` commit on
  `integration/3.0`, because that is the only place agents, CI and the internal submodule pin can
  see it. Parking it on a long-lived branch instead would stop the scheduled mirror sync from firing
  and strip CI from every feature PR — see the default-branch note above.
- **The branch is published by hand, not by the workflow**, as the last step of the two procedures
  that already force-push with human credentials (sections 3.3 and 7):

  ```bash
  git push --force origin <fork-base-sha>:refs/heads/fork-only/3.0
  ```

  This is not a style choice. The fork base is made almost entirely of files under
  `.github/workflows/`, and GitHub **blocks `GITHUB_TOKEN` from pushing any commit that creates or
  updates a workflow file** — a platform rule that no `permissions:` block can lift. Automating it
  would mean storing a PAT or GitHub App token with `workflows` write in a public repository, which
  is a far worse trade than one extra line in two procedures that are already manual.
- `.github/workflows/fork-only-view.yml` is therefore a **detector, not a publisher**, and needs no
  write access at all (`contents: read`). It re-derives the fork base, **fails if `fork-only/3.0` is
  missing or stale** and prints the exact command to fix it, refuses to pass a stack that violates
  the stack invariants in *Syncing and releasing*, and checks that the standing PR still has base `3.0`, draft status and
  the `do-not-merge` label. It runs on pushes to `integration/3.0` **and to `fork-only/3.0`** (so
  tampering with the view itself is caught), plus daily, plus on demand. Drift is loud, not silent.
- **The workflow does not open the PR either; a human does, once** — this repository deliberately
  withholds PR-creation rights from `GITHUB_TOKEN` (`can_approve_pull_request_reviews: false`).
  Afterwards the workflow only *checks* it: it warns when the standing PR is missing, and **fails
  loudly if one was ever merged**, which would mean the mirror is contaminated.
- **Never merge it, never commit to it.** Merging would push fork-only content into the mirror;
  committing to it would be silently discarded by the next republish.

**It carries `do-not-merge` alone, never `do-not-mirror`** — exactly like the integration draft PR
above, and for a mechanical reason: both labels *together* are the in-flight-process-change signal
that section 4.1 queries. A permanent PR carrying both would tell every agent, forever, that the
rules are mid-change. The rule is therefore general: **a standing artifact carries `do-not-merge`
alone; both labels together mark a `chore/<slug>` PR that is actively rewriting the process.**

Because the PR targets `3.0`, each refresh re-runs the `pull_request` workflows on it. That cost is
accepted: suppressing it would mean adding more fork-only hunks to `ci.yml`, `install.yml` and
`pre-commit.yml`, and every extra hunk in a shared file is another way a feature commit can get
contaminated. The fork base changes rarely, so the runs are rare too.

## Changing fork-only content

The "exactly one `chore(fork):` commit" rule is what keeps the stack trivially rebasable onto new
upstream, and keeps "is this commit contributable?" an obvious yes for every *other* commit on the
stack. Maintaining that invariant has consequences:

1. Branch `chore/<slug>` from `origin/integration/3.0`. Make the change as one commit.
2. Open the PR **as a draft**, titled `[do-not-mirror][do-not-merge] chore(fork): …`, and apply
   **both** the `do-not-mirror` and `do-not-merge` labels.
3. These are two independent axes and both must be marked, because they fail in different
   directions:
   - **`do-not-mirror`** — the change must never reach upstream. Marked four times over, deliberately
     redundantly: title prefix, label, `chore(fork):` commit type, and the "Never upstream" box in
     the PR template.
   - **`do-not-merge`** — the merge button must never be pressed on it. Merging would put a *second*
     `chore(fork):` commit on the stack, and `forkctl check-stack` asserts there is exactly one, so
     **every future sync would abort** until someone unpicked it by hand. Marked twice: title prefix
     and label.

   An agent that misses all of these is not reading.
4. Gates 1 and 2 (core section 4.8) apply in full. Gate 3 does not — see the exemption there.
5. When the gates pass, mark it ready for review, then **do not press the merge button.** Apply it
   by folding it into the base commit instead:

```bash
git switch integration/3.0
git fetch origin
test -z "$(git status --porcelain)" || { echo "ABORT: dirty tree"; exit 1; }

START=$(git rev-parse origin/integration/3.0)    # capture the lease target before rewriting
git reset --hard "$START"

# Confirms the stack is sound and returns the fork base, which is the oldest
# commit on it. With --print-base the report goes to stderr and only the SHA to stdout.
BASE=$(forkctl check-stack --stack HEAD --print-base) || exit 1

git cherry-pick --no-commit <chore-branch-commit-sha>
git commit --fixup "$BASE"
GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash origin/3.0
make test
```

Note `git cherry-pick` takes **one** commit — which is safe only because a `chore/<slug>` branch is
required to be exactly one commit (section 4.1). Confirm that before you start, or you will silently
apply only its tip.

Never use an interactive `git rebase -i` here. Agents run without an editor, where it silently
becomes a no-op — and the next command force-pushes the default branch. The `GIT_SEQUENCE_EDITOR=:`
form above is deterministic and needs no editor.

Verify the invariant actually held **before** you push:

```bash
# Re-checks every stack invariant, including that the fixup! was actually folded in,
# and returns the new fork base.
NEWBASE=$(forkctl check-stack --stack HEAD --print-base) || exit 1

# Move both refs or neither. The fold changes the fork base, so the view branch is stale the
# instant integration/3.0 lands; a two-step push leaves a window in which the detector reports
# drift that is already fixed, and a rejected lease must not move the view on its own.
git push --atomic --force-with-lease=refs/heads/integration/3.0:"$START" \
  origin integration/3.0 "+$NEWBASE:refs/heads/fork-only/3.0"
```

(Use the explicit `--force-with-lease=<ref>:<sha>` form — see *Syncing and releasing* for why the bare form is
unsafe. The shell-pitfall notes that used to live here went away with the shell: `forkctl` does the
checking, and its tests prove each guard fails when it should.)

This rewrites the default branch after only `make test`, which is narrower than the required check
set. Watch the standing draft PR afterwards and be ready to fix forward quickly.

The PR then ends up **closed, not merged**. Post the resulting `integration/3.0` SHA in the PR
thread so the trail survives; a closed-unmerged PR with no explanation looks like abandoned work.

**Keep the window short.** Between opening a `chore/<slug>` PR and folding it in, the default branch
still serves the *old* rules, and every agent that clones the fork in that window follows them — see
section 2. A fork-only PR is a **review vehicle, not a delivery mechanism**: nothing in it is in
force until the force-push above lands. So do not park one open across unrelated work, and when one
has been open a while, assume agents have already acted on the superseded text and check their
output against the new rules rather than assuming compliance.

If a feature genuinely needs to change one of these files, make it a **separate** `chore(fork):`
commit — do not fold it into the feature commit.

## Adversarial review

Every feature PR is adversarially reviewed before it may be stacked. The reviewer's job is to
**try to break the change**, not to confirm that it looks reasonable. "LGTM" is not a review.

**The reviewer is not the author.** When an agent wrote the change, the review runs as a *separate
task with fresh context* — a reviewer still holding the author's reasoning reproduces the author's
blind spots.

Work through all of these, and answer them in writing:

- **Does the test actually test it?** Revert the source change, keep the new test, confirm the test
  fails. A test that passes against unfixed code proves nothing.
- **What input breaks it?** Empty scenario, single agent, max agents, zero-length trajectory, NaN,
  out-of-range action, a controlled agent removed mid-episode.
- **Determinism.** Same seed, same result? Any new iteration over an unordered container,
  uninitialised read, or change to float accumulation order?
- **Trust boundaries and memory.** Every rule in `AGENTS.md` that touches the changed code —
  bounds, ownership, lifetime, null handling.
- **Layout and compatibility.** Observation vector, action space, config keys, checkpoint format.
  Any change here breaks submodule consumers and must be called out explicitly in the PR.
- **Performance.** Did a hot path gain an allocation, a branch, or a cache miss? If plausible,
  require `make -C tests/drive perf` numbers rather than an opinion.
- **Scope.** Anything in the diff that is not the one stated idea: drive-by renames, unrelated
  reformatting, leftover debug code, commented-out blocks.
- **Upstream reception.** Would an upstream maintainer request changes? Naming, file placement,
  config surface, a knob we want but they would not. Fix it now, not after they ask.

Post the outcome as a PR review listing what was probed and what was found — **including the probes
that found nothing**, so the next reader knows the coverage. Each finding is either fixed or
explicitly accepted by a human, with the reason in the thread.

## Mirror PR workflow (fork → upstream)

Gate 3 (section 4.8) already proved this commit cherry-picks cleanly. Do **not** assume the branch
it produced still exists: it is local-only, the feature branch is deleted from `origin` on merge,
and the agent opening the upstream PR is usually not the one that ran the gate. **Always rebuild the
mirror branch from scratch.**

Locate the commit **by subject, not by the SHA recorded on the feature PR**. Every sync rebase
rewrites the whole stack, so a recorded SHA stops resolving as soon as `3.0` advances. Treat it as a
hint for a human reader, never as an input to a command.

```bash
git fetch origin && git fetch upstream
forkctl mirror <slug> --subject "fix(scope): the exact commit subject" --stay
```

`--subject` resolves the commit against the live stack and aborts unless **exactly one** commit
matches, so an ambiguous or stale subject cannot silently mirror the wrong change. `--stay` leaves
you on `mirror/<slug>`, ready to push. The same fork-only file and marker guards as Gate 3 run
here: this is the authoritative check, not a formality, because the stack may have been rebased
since the gate last passed.

If a guard aborts, the feature commit was contaminated — fix it on `integration/3.0` and regenerate.
Otherwise push `mirror/<slug>` to `origin` and open the upstream PR from it **after a human has
verified the title, body and diff** (section 5).

Re-run the relevant suites from section 4.3 on the mirror branch: it is a different base from the
one CI tested, and "it passed on our stack" is not evidence that it passes on pure upstream.

Keep the upstream PR description in upstream's **What / Why / Notes** structure. Give them the
evidence, not our internal process vocabulary — upstream does not know or care what our integration
stack is.

**Match upstream's title convention, not ours.** This fork uses Conventional Commits; upstream does
not. Their merged PRs read as imperative sentence case — *"Remove native Drive rendering in favor of
HTML visualization"*, *"Add synchronized native and PyTorch profiling"*. A `fix(ci):` prefix marks
the PR as foreign on sight. The *commit* keeps our Conventional Commits subject; only the PR title
is restyled. Check their recent merged PRs before writing one:

```bash
gh pr list --repo Emerge-Lab/PufferDrive --state merged --limit 10 --json title --jq '.[].title'
```

**Expect no checks at first.** GitHub holds workflow runs on fork PRs from first-time contributors
in an `action_required` state until a maintainer presses *Approve and run workflows*. An upstream PR
reporting "no checks reported" is normal and is not a defect in the branch:

```bash
gh run list --repo Emerge-Lab/PufferDrive --limit 10 \
  --json conclusion,workflowName,headBranch --jq '.[] | select(.headBranch=="mirror/<slug>")'
```

Once approved, those runs use the workflow files from the PR's merge commit, so a workflow change we
are proposing is exercised by the very checks that gate it.

### Agent authorship and upstream's `AGENTS.md`

Upstream's `AGENTS.md` asks that an agent creating a PR add a file named `AGENTIC_PR` containing a
French poem. It is an agent-detection canary, and it collides with Gate 3, which requires the mirror
diff to contain nothing but the feature's own files.

**Our resolution: disclose instead of complying, and say that we are doing so.** Every upstream PR
body ends with a note to this effect:

> Prepared with AI assistance and reviewed by a human before submission. We noticed the `AGENTIC_PR`
> instruction in `AGENTS.md` — we are disclosing here rather than adding a poem file, to keep the
> diff limited to the change itself. Happy to add the file instead if you would prefer it.

This is a deliberate, declared deviation, and it is **not negotiable downward**: never omit the file
*and* omit the disclosure. That would be quietly defeating a detection mechanism the maintainers put
there on purpose, and the mirror-PR relationship depends entirely on them trusting what we send. If
upstream tells us they want the file, add it and update this section.

### While the mirror PR is open

A feature is normally **stacked on `integration/3.0` and open upstream at the same time**, often for
weeks. That overlap is deliberate, and sustaining it requires nothing: `mirror/<slug>` is parented
on `upstream/3.0`, never on `integration/3.0`, so rebasing the stack cannot touch it, and the
upstream PR (`ASCS-eV:mirror/<slug>` → `Emerge-Lab:3.0`) is unaffected by the fork PR being merged
or closed. Our fork deletes `feature/<slug>` automatically on merge; it does not touch `mirror/*`,
and upstream cannot delete branches that live in our fork.

So the default action during this period is **nothing**. Three events change that.

**What is open when.** The fork feature PR is *not* the long-lived artifact — it closes the moment
the feature stacks:

| Event | Fork feature PR | `mirror/<slug>` PR | Stack depth |
|---|---|---|---|
| Gates pass, human merges (4.8) | **merged, closed** | not open yet | +1 |
| Mirror pushed, upstream PR opened (6) | — | **open** | unchanged |
| Upstream merges | — | **closed by upstream** | unchanged |
| Next sync + rebase (7) | — | — | **−1**, drops as empty |

The fork PR **cannot** be held open as a tracker: once its commit is reachable from
`integration/3.0`, GitHub marks the PR merged on its own. Anything that needs to outlive stacking —
tracking, discussion, the upstream conversation — belongs on the mirror PR, which is why the
inventory below keys off `mirror/*` rather than off feature branches.

**Inventory — what is in flight.** The fork PR is closed by now, so nothing in `integration/3.0`
points at the open upstream PR; the slug is the only key. Run this before any sync or release:

```bash
git fetch origin && git fetch upstream
forkctl status
```

It lists every `mirror/*` branch on `origin` with the open upstream PR that belongs to it, and for
each one checks the only two events that need action:

- **Event 1 — `upstream/3.0` advanced into a conflict.** The mirror no longer merges onto the
  upstream tip, so the PR shows conflicts and reviewers can no longer read it. An upstream advance
  that still merges cleanly needs no action and is reported as nothing.
- **Event 2 — our stacked commit changed.** A sync rebase can alter a commit through conflict
  resolution, leaving the open PR offering upstream something we no longer run. That is a
  correctness and honesty problem, not untidiness. It is detected by patch-identity against the
  live stack, so a renamed subject does not raise a false alarm and an amended diff does not hide.

A `mirror/*` branch with no open PR is either finished (*After upstream responds*) or was never opened. An open PR
with no branch should be impossible and means someone hit the deletion hazard below.

For either event, rebuild with the recipe in section 6 of the core instructions — including its guards, because a conflict
resolution is exactly where fork-only content gets dragged in — then:

```bash
git push --force-with-lease origin mirror/<slug>
```

Force-pushing updates the open PR in place. **Never close and reopen**: that discards the review
thread and reads as a withdrawal. After an Event 2 rebuild, say in the PR that the change was
updated and why; never leave upstream reviewing a version we have abandoned.

**Hazard — deleting `mirror/<slug>` closes the upstream PR.** GitHub closes a pull request when its
head branch disappears. Done to an open contribution, that looks like a silent withdrawal and throws
away the review. *After upstream responds* ends in a branch deletion, which is safe *only* once the PR has reached
a terminal state. Never delete a mirror branch by hand; the guard is built in:

```bash
forkctl delete-mirror <slug>     # --dry-run to check without deleting
```

It refuses unless the upstream PR is `MERGED`, `CLOSED`, or was never opened.

### After upstream responds

A mirror PR has three possible endings, and each leaves different work behind. None of them is
"nothing" — a stacked commit whose mirror has been resolved must be retired deliberately, or the
stack slowly fills with commits nobody can explain.

**First, find out what actually happened.** Patch-identity, not the PR's merge badge, is the test —
upstream may squash, rename or amend, and a merged PR does not guarantee our exact diff landed.

```bash
git fetch upstream
git cherry upstream/3.0 mirror/<slug>
#   "- <sha>"   -> an equivalent patch IS upstream under a different SHA   (case A)
#   "+ <sha>"   -> it is NOT upstream as written                          (case B or C)
#   (no output) -> our exact commit is now an ancestor of upstream/3.0    (case A)
```

**Empty output is a result, not a failure.** It means upstream merged in a way that preserved our
commit object, so it is reachable from `upstream/3.0` and falls outside the range `git cherry`
examines. Both `-` and silence mean "landed"; only `+` means "not landed as written". If you cannot
tell which you are looking at, disambiguate explicitly:

```bash
git merge-base --is-ancestor mirror/<slug> upstream/3.0 \
  && echo "landed verbatim (same commit)" \
  || git cherry upstream/3.0 mirror/<slug>
```

Patch-identity ignores the commit message, so upstream renaming the subject still reports `-`. It
does **not** ignore content: any amendment to the diff, however small, reports `+`. That is the
correct signal — an amended change is case B, and the rebase will stop on it rather than drop it.

The same question across the whole stack is `forkctl status`, which lists every commit we carry
that upstream already has. Those are pure rebase risk and should already be gone; if one persists
across two syncs, something is resurrecting it (*Syncing and releasing*). Run it before any release.

**Case A — merged as written.** Do nothing to the stack by hand. The next sync fast-forwards `3.0`,
and the rebase of `integration/3.0` drops our copy automatically because it becomes empty. Confirm
it actually went, rather than assuming — `forkctl status` must report a feature gap one lower than
before, and no stale commits:

```bash
forkctl status
git branch -D mirror/<slug>
```

Then delete the remote branch with `forkctl delete-mirror <slug>` — never with a bare
`git push origin --delete`. In case A the PR is already merged, so the guard passes; running it
anyway is what stops the same command being used by reflex while a PR is still open.

**Case B — merged with modifications.** Upstream's edited version is now in the new base, so ours is
redundant *and* textually different, which means the rebase will stop on a conflict instead of
dropping it silently. Resolve in favour of upstream — ours has no privileged status once they have
taken ownership of the change:

```bash
git rebase --skip     # discards our now-superseded commit and continues
```

Then read upstream's merged version before moving on. If they changed behaviour we depend on, that
is a *new* feature branch against the new base, not an attempt to restore our original commit. Never
resurrect a superseded commit to "win back" a difference; raise it upstream as a follow-up instead.

**Case C — rejected, or stalled with no realistic path.** This is the one that needs a decision
rather than a command, and it must not be left implicit. The commit is now a **permanent fork
delta**, and a permanent delta that still looks like a pending contribution will be rebased,
re-reviewed and re-offered by some future agent forever.

Decide explicitly, and record the decision in the PR and in the commit subject:

- **Keep it as a fork-only behaviour change** — reclassify it under core section 3.2, add its path or
  marker to `FORK_ONLY_PATHS` / `FORK_HUNK_MARKERS` in `.github/fork/forkctl.py` (with a test), and
  rename the commit to `chore(fork):` so the core section 4.5 and Gate 3 guards start treating it as
  something that must *never* leave. This is the honest outcome for a change upstream has declined
  on principle.
- **Drop it** — if it only existed to be upstreamed, remove it from the stack (*Changing fork-only content*) rather
  than carrying a rebase hazard indefinitely.

Either way, close the mirror PR with a reason, delete `mirror/<slug>`, and note the outcome so the
next agent does not reopen the same conversation with upstream.

**In all three cases**, if the feature commit was rewritten on our side at any point after the
mirror branch was built, that mirror is stale — rebuild it and re-prove Gate 3 (core section 6) before
drawing any conclusion from `git cherry`, which compares the stale patch rather than the live one.

## Syncing and releasing

**Sync the mirror:** automated. `.github/workflows/sync-mirror.yml` fast-forwards `3.0` from
`upstream/3.0` daily and on demand (Actions → *Sync 3.0 mirror* → *Run workflow*). It refuses any
non-fast-forward, which is the alarm that someone committed to the mirror. `3.0` additionally blocks
force-pushes, deletions, and non-linear history. Do not push to it by hand.

When the mirror moves ahead of the stack, the same workflow opens (and keeps updated) a
`mirror-sync` tracking issue; it closes the issue automatically once `integration/3.0` contains the
mirror head. The rebase itself stays manual — replaying features onto new upstream code can
conflict and must be re-tested.

**Rebase the stack** (maintainers):

```bash
git switch integration/3.0
git fetch origin
test -z "$(git status --porcelain)" || { echo "ABORT: dirty tree"; exit 1; }

START=$(git rev-parse origin/integration/3.0)    # capture the lease target before rewriting
git reset --hard "$START"                        # discard local drift
git rebase origin/3.0
```

On conflict, resolve in favour of upstream for code they changed, keep our commit's intent, then:

```bash
git add <resolved-paths>
GIT_EDITOR=true git rebase --continue    # --continue opens an editor; GIT_EDITOR=false makes it fail
```

If a conflict cannot be resolved confidently, `git rebase --abort` and raise it — never guess on the
branch BMW pins.

Re-check the invariants **after** the rebase, exactly as in *Changing fork-only content* — a rebase happily preserves
a stack that was already malformed:

```bash
BASE=$(forkctl check-stack --stack HEAD --print-base) || exit 1

make test

# Move both refs or neither. A rejected lease must not leave the view branch pointing at a base
# that was never published, and a two-step push leaves a window in which the detector reports
# drift that no longer exists.
git push --atomic --force-with-lease=refs/heads/integration/3.0:"$START" \
  origin integration/3.0 "+$BASE:refs/heads/fork-only/3.0"
```

Use the **explicit** `--force-with-lease=<ref>:<sha>` form. Bare `--force-with-lease` leases against
your remote-tracking ref, which any background `git fetch` silently refreshes — at which point it
will happily overwrite a colleague's push that you never incorporated. If the push is rejected,
start over from the new remote tip; do not re-run with a wider hammer.

`make test` is not the full gate (section 4.3) — it skips Docker smoke, perf, the install matrix and
pre-commit. After pushing, the standing draft PR re-runs the real suite against the rewritten stack;
treat a release as blocked until that is green.

Drop commits that upstream has already merged; never resurrect them — *After upstream responds* gives the
mechanics, including the case where upstream merged a *modified* version. After any rebase that
rewrote a feature commit, its `mirror/<slug>` branch is stale: if an upstream PR is already open on
it, *While the mirror PR is open* applies and the PR must be refreshed; if not, Gate 3 must be re-proven before that
feature is offered upstream. Either way, a commit whose content changed materially needs re-review.

**Divergence report.** The stack is upstream *debt*: every feature commit we carry is one upstream
has not taken yet. Run this after every sync, and before any release:

```bash
git fetch origin && git fetch upstream
forkctl status
```

It prints the total gap, the fork-only floor and the feature gap, lists the stack, warns about
commits upstream already has, and reports every in-flight mirror. It warns rather than fails: a
non-zero gap is the normal state, not an error.

**The total gap can never reach zero, and must not be driven to zero.** `integration/3.0`
permanently carries at least one `chore(fork):` commit, because the files in section 3.1 do not
exist upstream by design. An agent that reads a non-zero gap as a defect and tries to close it by
upstreaming that commit would leak the entire fork-only process into `Emerge-Lab/PufferDrive` —
precisely what Gate 3 exists to prevent.

**The number that should trend to zero is the feature gap.** Zero means upstream has absorbed
everything we carry and the fork is pure scaffolding again; that is the steady state we are aiming
for, not an exceptional one. A feature gap that only grows means we are accumulating a private fork
rather than contributing — treat a persistently rising count as a process failure to discuss, not a
number to ignore. Every feature commit on the stack should be traceable to an open or pending mirror
PR (*While the mirror PR is open*); one that is not is either unfinished work or a change nobody intends to upstream,
and the second kind belongs in a `chore(fork):` commit where the guards can see it.

**Hazard — `update-smoke-golden.yml`.** This inherited upstream workflow ends in
`git push origin HEAD:<ref>`, and it triggers on pushes to `3.0` as well as `workflow_dispatch`.
Never dispatch it against `3.0`: it would commit to the mirror and break the clean base for every
upstream PR. If it ever does, `sync-mirror.yml` is the detector — it refuses the non-fast-forward
and the mirror-sync issue appears. Recover by resetting `3.0` to `upstream/3.0` (the branch
protection on `3.0` blocks force-pushes, so this needs an admin and should be done deliberately).

**Release for the BMW submodule:** after a green rebase, run the divergence report above and confirm
the feature gap matches the mirror PRs actually in flight — a release is the moment the gap becomes
someone else's dependency. Then tag an immutable point and pin that tag.

```bash
git tag -a ascs/3.0-$(date +%Y%m%d) -m "Integration snapshot"
git push origin ascs/3.0-$(date +%Y%m%d)
```

Consumers pin a tag or a commit SHA — never the moving `integration/3.0` tip.
