#!/usr/bin/env python3
"""Tests for forkctl. Fork-only; never cherry-pick upstream.

Run: python .github/fork/test_forkctl.py

Every guard is asserted in both directions -- it passes a well-formed stack and
it *fails* a malformed one. A guard that cannot be shown to fail is not a guard,
and these particular guards are what stand between a bad commit and the branch
BMW pins, so they are exercised against throwaway repositories built to break
them rather than against prose.

Stdlib only, so it runs anywhere git does, with no test dependencies.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import forkctl


FEATURE = "feat(drive): add a thing"
CHORE = "chore(fork): define the ASCS-eV fork process"

# The fixture repo is built once and copied per test. Spawning git costs ~0.2s
# on a Windows checkout with live virus scanning, so rebuilding it per test put
# the suite over five minutes; copying the directory keeps full isolation.
_TEMPLATE: tempfile.TemporaryDirectory | None = None
_TEMPLATE_REPO: Path | None = None
_BASE_SHA = ""
_FEATURE_SHA = ""


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def write(repo: Path, relpath: str, text: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, subject: str, files: dict[str, str]) -> str:
    for relpath, text in files.items():
        write(repo, relpath, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def run(*argv: str) -> tuple[int, str]:
    """Invoke forkctl in-process and capture what it printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = forkctl.main(list(argv))
    return code, buf.getvalue()


CI_YML_UPSTREAM = """\
name: CI

on:
  pull_request:
    branches: ['main', '3.0']
  push:
    branches: ['main', '3.0']

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo build
"""

CI_YML_FORKED = CI_YML_UPSTREAM.replace(
    "  pull_request:\n    branches: ['main', '3.0']",
    "  pull_request:\n    branches: ['main', '3.0', 'integration/3.0']",
)

INSTALL_YML_UPSTREAM = CI_YML_UPSTREAM.replace("name: CI", "name: Installation Test")
INSTALL_YML_FORKED = CI_YML_FORKED.replace("name: CI", "name: Installation Test")

# The fork base adds exactly one block to each shared doc, and nothing else.
AGENTS_UPSTREAM = "## Coding Standards\nexplicit names\n"
AGENTS_FORKED = AGENTS_UPSTREAM + "\n<!-- fork:begin -->\nfork process\n<!-- fork:end -->\n"


def upstream_tree() -> dict[str, str]:
    return {
        "README.md": "upstream\n",
        "AGENTS.md": AGENTS_UPSTREAM,
        "CLAUDE.md": AGENTS_UPSTREAM,
        ".github/workflows/ci.yml": CI_YML_UPSTREAM,
        ".github/workflows/install.yml": INSTALL_YML_UPSTREAM,
    }


def fork_base_tree() -> dict[str, str]:
    """Everything the real fork base carries, including every required file.

    The required-file invariant is only meaningful if the well-formed fixture
    actually satisfies it; an incomplete fixture made `check-stack` pass on a
    base that had lost the detector.
    """
    tree = {
        "AGENTS.md": AGENTS_FORKED,
        "CLAUDE.md": AGENTS_FORKED,
        ".github/workflows/ci.yml": CI_YML_FORKED,
        ".github/workflows/install.yml": INSTALL_YML_FORKED,
    }
    for path in forkctl.REQUIRED_FORK_FILES:
        tree[path] = f"fork-only: {path}\n"
    return tree


def setUpModule() -> None:
    global _TEMPLATE, _TEMPLATE_REPO, _BASE_SHA, _FEATURE_SHA
    _TEMPLATE = tempfile.TemporaryDirectory()
    repo = Path(_TEMPLATE.name) / "template"
    repo.mkdir()

    git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Fork Test")
    git(repo, "config", "commit.gpgsign", "false")

    commit(repo, "upstream: initial", upstream_tree())
    git(repo, "branch", "up")
    git(repo, "branch", "mir")

    git(repo, "switch", "-q", "-c", "stack", "mir")
    _BASE_SHA = commit(repo, CHORE, fork_base_tree())
    _FEATURE_SHA = commit(repo, FEATURE, {"sim.c": "int main(void){}\n"})
    _TEMPLATE_REPO = repo


def tearDownModule() -> None:
    if _TEMPLATE is not None:
        _TEMPLATE.cleanup()


class ForkRepoTest(unittest.TestCase):
    """A throwaway repo shaped like the real fork: mirror + stack + upstream."""

    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        shutil.copytree(_TEMPLATE_REPO, self.repo)
        self.base = _BASE_SHA
        self.feature = _FEATURE_SHA
        os.chdir(self.repo)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def branch_from_stack(self, name: str) -> None:
        git(self.repo, "switch", "-q", "-c", name, "stack")

    def stacked_feature(self, subject: str, tree: dict[str, str], name: str = "feature/x") -> str:
        """A one-commit branch on top of the stack tip, exactly as Gate 3 expects."""
        self.branch_from_stack(name)
        return commit(self.repo, subject, tree)


class TestCheckStack(ForkRepoTest):
    def test_passes_on_a_well_formed_stack(self) -> None:
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 0, out)
        self.assertIn("exactly one chore(fork): commit", out)

    def test_prints_the_fork_base(self) -> None:
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack", "--print-base")
        self.assertEqual(code, 0, out)
        self.assertIn(self.base, out)

    def test_fails_on_a_second_chore_commit(self) -> None:
        git(self.repo, "switch", "-q", "stack")
        commit(self.repo, "chore(fork): second one", {"CONTRIBUTING.md": "again\n"})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("2 chore(fork): commits", out)

    def test_fails_on_a_merge_commit(self) -> None:
        git(self.repo, "switch", "-q", "-c", "side", "mir")
        commit(self.repo, "side work", {"side.txt": "x\n"})
        git(self.repo, "switch", "-q", "stack")
        git(self.repo, "merge", "--no-ff", "-m", "merge side", "side")
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("merge commit on the stack", out)

    def test_fails_on_a_stray_fixup_commit(self) -> None:
        git(self.repo, "switch", "-q", "stack")
        commit(self.repo, f"fixup! {CHORE}", {"CONTRIBUTING.md": "wip\n"})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("stray fixup!/squash! commit", out)

    def test_fails_when_the_base_is_not_the_fork_commit(self) -> None:
        git(self.repo, "switch", "-q", "-c", "wrong", "mir")
        commit(self.repo, "feat(x): not the fork base", {"a.txt": "a\n"})
        commit(self.repo, CHORE, {"CONTRIBUTING.md": "fork\n"})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "wrong")
        self.assertEqual(code, 1, out)
        self.assertIn("is not the fork base", out)

    def test_fails_when_the_stack_does_not_contain_the_mirror_tip(self) -> None:
        git(self.repo, "switch", "-q", "mir")
        commit(self.repo, "upstream: moved on", {"README.md": "newer\n"})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("does not contain", out)

    def test_fails_on_a_missing_ref(self) -> None:
        code, out = run("check-stack", "--mirror", "nope", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("not found locally", out)

    def test_github_format_emits_annotations(self) -> None:
        git(self.repo, "switch", "-q", "stack")
        commit(self.repo, "chore(fork): second one", {"CONTRIBUTING.md": "again\n"})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack", "--format", "github")
        self.assertEqual(code, 1, out)
        self.assertIn("::error::", out)


class TestCheckCommit(ForkRepoTest):
    def test_passes_for_a_clean_single_commit_feature(self) -> None:
        self.branch_from_stack("feature/clean")
        commit(self.repo, "feat(obs): lane heading", {"obs.c": "x\n"})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 0, out)

    def test_fails_on_two_commits(self) -> None:
        self.branch_from_stack("feature/two")
        commit(self.repo, "feat(obs): one", {"obs.c": "x\n"})
        commit(self.repo, "feat(obs): two", {"obs2.c": "y\n"})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("2 commits on the branch", out)

    def test_fails_when_a_fork_only_file_is_touched(self) -> None:
        self.branch_from_stack("feature/dirty")
        commit(
            self.repo,
            "feat(obs): sneaky",
            {"obs.c": "x\n", "CONTRIBUTING.md": "edited\n"},
        )
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("fork-only file(s) in the feature diff", out)
        self.assertIn("CONTRIBUTING.md", out)

    def test_fails_for_a_new_file_under_the_fork_only_prefix(self) -> None:
        self.branch_from_stack("feature/newtool")
        commit(self.repo, "feat(x): tool", {".github/fork/helper.py": "print()\n"})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn(".github/fork/helper.py", out)

    def test_fails_when_content_inside_the_fork_block_is_edited(self) -> None:
        """The changed lines carry no marker, so a diff-line scan misses this."""
        self.branch_from_stack("feature/hunk")
        commit(
            self.repo,
            "feat(x): edit the fork block",
            {"AGENTS.md": "style\n<!-- fork:begin -->\nCHANGED\n<!-- fork:end -->\n"},
        )
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("fork-only block content changed in: AGENTS.md", out)

    def test_fails_when_the_fork_block_is_removed(self) -> None:
        self.branch_from_stack("feature/deblock")
        commit(self.repo, "feat(x): drop the fork block", {"AGENTS.md": "style\n"})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("fork-only", out)

    def test_fails_when_the_fork_branch_filter_is_removed(self) -> None:
        self.branch_from_stack("feature/filter-drop")
        commit(self.repo, "ci(x): tidy the filter", {".github/workflows/ci.yml": CI_YML_UPSTREAM})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("removed from `pull_request`", out)

    def test_fails_when_the_fork_branch_filter_moves_to_push(self) -> None:
        """A move leaves the marker line intact, so only a structural read catches it."""
        self.branch_from_stack("feature/filter-move")
        moved = CI_YML_UPSTREAM.replace(
            "  push:\n    branches: ['main', '3.0']",
            "  push:\n    branches: ['main', '3.0', 'integration/3.0']",
        )
        commit(self.repo, "ci(x): move the filter", {".github/workflows/ci.yml": moved})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("added to `push`", out)

    def test_allows_editing_the_rest_of_a_shared_file(self) -> None:
        self.branch_from_stack("feature/shared-ok")
        commit(
            self.repo,
            "docs(agents): clarify naming",
            {"AGENTS.md": AGENTS_FORKED.replace("explicit names", "explicit, unit-suffixed names")},
        )
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 0, out)

    def test_chore_mode_exempts_the_fork_only_checks(self) -> None:
        self.branch_from_stack("chore/process")
        commit(self.repo, "chore(fork): rewrite", {"CONTRIBUTING.md": "new\n"})
        code, out = run("check-commit", "--base", "stack", "--chore")
        self.assertEqual(code, 0, out)
        self.assertIn("exempt", out)

    def test_chore_mode_still_enforces_one_commit(self) -> None:
        self.branch_from_stack("chore/two")
        commit(self.repo, "chore(fork): one", {"CONTRIBUTING.md": "a\n"})
        commit(self.repo, "chore(fork): two", {"CONTRIBUTING.md": "b\n"})
        code, out = run("check-commit", "--base", "stack", "--chore")
        self.assertEqual(code, 1, out)
        self.assertIn("2 commits on the branch", out)

    def test_chore_mode_rejects_a_non_chore_subject(self) -> None:
        self.branch_from_stack("chore/mislabelled")
        commit(self.repo, "feat(x): not a chore", {"CONTRIBUTING.md": "a\n"})
        code, out = run("check-commit", "--base", "stack", "--chore")
        self.assertEqual(code, 1, out)
        self.assertIn("--chore given but the subject", out)

    def test_warns_when_a_chore_commit_is_checked_without_chore(self) -> None:
        self.branch_from_stack("chore/forgot-flag")
        commit(self.repo, "chore(fork): rewrite", {"CONTRIBUTING.md": "new\n"})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("WARN", out)


class TestMirror(ForkRepoTest):
    def test_clean_feature_passes_gate_three(self) -> None:
        sha = self.stacked_feature("feat(x): clean work", {"clean.c": "x\n"})
        code, out = run(
            "mirror",
            "clean",
            "--sha",
            sha,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("cherry-picks onto pure upstream without conflict", out)
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "feature/x")

    def test_resolves_the_commit_by_subject(self) -> None:
        code, out = run(
            "mirror",
            "clean",
            "--subject",
            FEATURE,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 0, out)

    def test_rejects_an_ambiguous_subject(self) -> None:
        git(self.repo, "switch", "-q", "stack")
        commit(self.repo, FEATURE, {"other.c": "y\n"})
        code, out = run(
            "mirror",
            "dupe",
            "--subject",
            FEATURE,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 1, out)
        self.assertIn("matches 2 stack commits", out)

    def test_fails_when_the_commit_adds_a_fork_only_file(self) -> None:
        sha = self.stacked_feature(
            "feat(x): contaminated",
            {"x.c": "x\n", ".github/fork/scratch-notes.md": "fork-only\n"},
        )
        code, out = run(
            "mirror",
            "bad",
            "--sha",
            sha,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 1, out)
        self.assertIn("fork-only file(s) in the mirror diff", out)

    def test_modifying_a_fork_only_file_conflicts_on_pure_upstream(self) -> None:
        """Such a commit cannot even apply upstream -- the file is not there."""
        sha = self.stacked_feature(
            "feat(x): edits a fork-only file",
            {"x.c": "x\n", "CONTRIBUTING.md": "touched\n"},
        )
        code, out = run(
            "mirror",
            "bad2",
            "--sha",
            sha,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 1, out)
        self.assertIn("entangled with fork-only state", out)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_fails_when_the_commit_adds_a_fork_only_marker(self) -> None:
        sha = self.stacked_feature(
            "ci(x): add a workflow gated on the fork branch",
            {".github/workflows/extra.yml": "on:\n  push:\n    branches: ['integration/3.0']\n"},
        )
        code, out = run(
            "mirror",
            "marker",
            "--sha",
            sha,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 1, out)
        self.assertIn("fork-only marker", out)
        code, out = run(
            "mirror",
            "marker",
            "--sha",
            sha,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 1, out)
        self.assertIn("fork-only marker", out)

    def test_aborts_and_restores_the_branch_on_conflict(self) -> None:
        git(self.repo, "switch", "-q", "up")
        commit(self.repo, "upstream: conflicting edit", {"sim.c": "DIFFERENT\n"})
        sha = self.stacked_feature("feat(x): conflicting work", {"sim.c": "OURS\n"})
        code, out = run(
            "mirror",
            "conflict",
            "--sha",
            sha,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 1, out)
        self.assertIn("entangled with fork-only state", out)
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "feature/x")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_rebuilds_an_existing_mirror_branch(self) -> None:
        git(self.repo, "branch", "mirror/clean", "up")
        sha = self.stacked_feature("feat(x): clean work", {"clean.c": "x\n"})
        code, out = run(
            "mirror",
            "clean",
            "--sha",
            sha,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 0, out)

    def test_refuses_to_run_with_a_dirty_tree(self) -> None:
        write(self.repo, "scratch.txt", "uncommitted\n")
        code, out = run(
            "mirror",
            "clean",
            "--sha",
            self.feature,
            "--upstream",
            "up",
            "--mirror",
            "mir",
            "--stack",
            "stack",
        )
        self.assertEqual(code, 1, out)
        self.assertIn("dirty", out)


class TestMirrorDrift(ForkRepoTest):
    """Section 6.2: the two events that make an open upstream PR wrong."""

    def stack_patch_ids(self) -> set[str]:
        ids = set()
        for sha in git(self.repo, "rev-list", "mir..stack").split():
            identity = forkctl.patch_id(sha)
            if identity is not None:
                ids.add(identity)
        return ids

    def test_a_fresh_mirror_reports_no_drift(self) -> None:
        git(self.repo, "switch", "-q", "-c", "mirror/clean", "up")
        git(self.repo, "cherry-pick", self.feature)
        git(self.repo, "switch", "-q", "stack")
        self.assertEqual(forkctl.mirror_drift("clean", "up", self.stack_patch_ids()), [])

    def test_event_one_fires_when_upstream_conflicts(self) -> None:
        git(self.repo, "switch", "-q", "-c", "mirror/clean", "up")
        git(self.repo, "cherry-pick", self.feature)
        git(self.repo, "switch", "-q", "up")
        commit(self.repo, "upstream: rewrite the same file", {"sim.c": "int main(void){return 7;}\n"})
        git(self.repo, "switch", "-q", "stack")
        notes = forkctl.mirror_drift("clean", "up", self.stack_patch_ids())
        self.assertTrue(any("event 1" in note for note in notes), notes)

    def test_an_unrelated_upstream_advance_is_not_drift(self) -> None:
        """A readable PR needs no action, however far upstream has moved."""
        git(self.repo, "switch", "-q", "-c", "mirror/clean", "up")
        git(self.repo, "cherry-pick", self.feature)
        git(self.repo, "switch", "-q", "up")
        commit(self.repo, "upstream: unrelated progress", {"NOTICE": "new\n"})
        git(self.repo, "switch", "-q", "stack")
        self.assertEqual(forkctl.mirror_drift("clean", "up", self.stack_patch_ids()), [])

    def test_event_two_fires_when_the_stack_commit_was_amended(self) -> None:
        git(self.repo, "switch", "-q", "-c", "mirror/clean", "up")
        git(self.repo, "cherry-pick", self.feature)
        git(self.repo, "switch", "-q", "stack")
        write(self.repo, "sim.c", "int main(void){return 1;}\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--amend", "--no-edit")
        notes = forkctl.mirror_drift("clean", "up", self.stack_patch_ids())
        self.assertTrue(any("event 2" in note for note in notes), notes)

    def test_renaming_the_subject_alone_is_not_drift(self) -> None:
        """Patch-identity ignores the message, so upstream renaming is quiet."""
        git(self.repo, "switch", "-q", "-c", "mirror/clean", "up")
        git(self.repo, "cherry-pick", self.feature)
        git(self.repo, "commit", "-q", "--amend", "-m", "Add a thing to the simulator")
        git(self.repo, "switch", "-q", "stack")
        self.assertEqual(forkctl.mirror_drift("clean", "up", self.stack_patch_ids()), [])

    def test_a_missing_mirror_branch_is_reported(self) -> None:
        notes = forkctl.mirror_drift("absent", "up", self.stack_patch_ids())
        self.assertTrue(any("not fetched" in note for note in notes), notes)


class TestReviewRegressions(ForkRepoTest):
    """One test per bypass found in the Gate 2 adversarial review of PR #9."""

    def feature_branch(self, name: str = "feature/x") -> None:
        self.branch_from_stack(name)

    def test_renaming_a_fork_only_file_away_is_caught(self) -> None:
        """Rename detection reports only the destination, hiding the source."""
        self.feature_branch()
        git(self.repo, "mv", "CONTRIBUTING.md", "FORK_GUIDE.md")
        git(self.repo, "commit", "-q", "-m", "docs(x): rename the guide")
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("CONTRIBUTING.md", out)

    def test_moving_the_branch_filter_to_push_is_caught(self) -> None:
        """The marker line is unchanged, so only a structural read catches it."""
        self.feature_branch()
        moved = CI_YML_FORKED.replace(
            "  pull_request:\n    branches: ['main', '3.0', 'integration/3.0']\n  push:\n    branches: ['main', '3.0']",
            "  pull_request:\n    branches: ['main', '3.0']\n  push:\n    branches: ['main', '3.0', 'integration/3.0']",
        )
        self.assertNotEqual(moved, CI_YML_FORKED)
        commit(self.repo, "ci(x): retune triggers", {".github/workflows/ci.yml": moved})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("pull_request", out)

    def test_editing_the_rest_of_a_shared_workflow_is_allowed(self) -> None:
        self.feature_branch()
        commit(
            self.repo,
            "ci(x): add a step",
            {".github/workflows/ci.yml": CI_YML_FORKED.replace("echo build", "echo build && echo more")},
        )
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 0, out)

    def test_a_duplicate_fork_block_is_rejected(self) -> None:
        """A second block would be unprotected: fork_blocks compared only one."""
        self.feature_branch()
        commit(
            self.repo,
            "docs(x): touch agents",
            {
                "AGENTS.md": "style\n<!-- fork:begin -->\nfork\n<!-- fork:end -->\nmore\n<!-- fork:begin -->\nb\n<!-- fork:end -->\n"
            },
        )
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("marker structure", out)

    def test_a_stale_base_is_rejected(self) -> None:
        """One commit *ahead* is not the same as rebased onto the current tip."""
        self.feature_branch()
        commit(self.repo, "feat(x): mine", {"mine.c": "x\n"})
        git(self.repo, "switch", "-q", "stack")
        commit(self.repo, "feat(y): someone else landed first", {"theirs.c": "y\n"})
        git(self.repo, "switch", "-q", "feature/x")
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("not parented on the current", out)

    def test_a_non_conventional_subject_is_rejected(self) -> None:
        self.feature_branch()
        commit(self.repo, "totally invalid subject", {"a.c": "x\n"})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("Conventional Commits", out)

    def test_an_over_long_subject_is_rejected(self) -> None:
        self.feature_branch()
        commit(self.repo, "feat(drive): " + "x" * 70, {"a.c": "x\n"})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("72-char limit", out)

    def test_a_leading_space_subject_cannot_pose_as_the_fork_base(self) -> None:
        git(self.repo, "switch", "-q", "-c", "stack-leading", "mir")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", " chore(fork): leading")
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack-leading")
        self.assertEqual(code, 1, out)
        self.assertIn("stray whitespace", out)

    def test_a_leading_space_hides_nothing_from_the_fixup_check(self) -> None:
        self.branch_from_stack("stack-fixup")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "  fixup! chore(fork): x")
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack-fixup")
        self.assertEqual(code, 1, out)
        self.assertIn("fixup", out)

    def test_an_unregistered_file_in_the_fork_base_is_caught(self) -> None:
        """Keeps the hardcoded fork-only list honest as the base grows."""
        git(self.repo, "switch", "-q", "-c", "stack-unreg", "mir")
        commit(self.repo, CHORE, {".github/workflows/sync-two.yml": "name: x\n"})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack-unreg")
        self.assertEqual(code, 1, out)
        self.assertIn("unregistered", out)

    def test_gate_three_refuses_an_arbitrary_commit(self) -> None:
        """--sha must prove the commit being merged, not any commit that exists."""
        git(self.repo, "switch", "-q", "-c", "elsewhere", "up")
        outsider = commit(self.repo, "feat(x): unrelated external commit", {"x.c": "x\n"})
        code, out = run("mirror", "arb", "--sha", outsider, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("not a single commit on top of", out)

    def test_gate_three_refuses_a_commit_that_is_not_checked_out(self) -> None:
        git(self.repo, "switch", "-q", "-c", "elsewhere-2", "up")
        outsider = commit(self.repo, "feat(x): unrelated external commit", {"x.c": "x\n"})
        git(self.repo, "switch", "-q", "stack")
        code, out = run("mirror", "arb", "--sha", outsider, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("is not HEAD", out)

    def test_gate_three_accepts_a_one_commit_feature_branch(self) -> None:
        self.feature_branch()
        sha = commit(self.repo, "feat(x): on the tip", {"tip.c": "x\n"})
        code, out = run("mirror", "tip", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 0, out)

    def test_a_failed_mirror_leaves_the_existing_branch_intact(self) -> None:
        """The old tip may be what an open upstream PR is serving."""
        git(self.repo, "switch", "-q", "-c", "mirror/keep", "up")
        kept = commit(self.repo, "feat(x): already published", {"published.c": "x\n"})
        bad = self.stacked_feature("feat(x): drags in fork state", {"CONTRIBUTING.md": "changed\n"})
        code, out = run("mirror", "keep", "--sha", bad, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertEqual(git(self.repo, "rev-parse", "mirror/keep"), kept)

    def test_mirror_refuses_a_detached_head_without_moving_it(self) -> None:
        git(self.repo, "switch", "-q", "--detach", "stack")
        started = git(self.repo, "rev-parse", "HEAD")
        code, out = run(
            "mirror", "det", "--sha", self.feature, "--upstream", "up", "--mirror", "mir", "--stack", "stack"
        )
        self.assertEqual(code, 1, out)
        self.assertIn("HEAD is detached", out)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), started)

    def test_drift_reports_an_extra_commit_on_the_mirror(self) -> None:
        git(self.repo, "switch", "-q", "-c", "mirror/extra", "up")
        commit(self.repo, "feat(x): smuggled extra", {"extra.c": "x\n"})
        git(self.repo, "cherry-pick", self.feature)
        git(self.repo, "switch", "-q", "stack")
        ids = {forkctl.patch_id(s) for s in git(self.repo, "rev-list", "mir..stack").split()}
        notes = forkctl.mirror_drift("extra", "up", {i for i in ids if i})
        self.assertTrue(any("expected exactly 1" in note for note in notes), notes)

    def test_prose_mentioning_the_fork_branch_does_not_block_a_mirror(self) -> None:
        """`integration/3.0` is only evidence inside CI configuration."""
        self.feature_branch()
        sha = commit(self.repo, "docs(x): describe branches", {"README.md": "we also run integration/3.0 here\n"})
        code, out = run("mirror", "prose", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 0, out)

    def test_a_fork_marker_in_prose_still_blocks_a_mirror(self) -> None:
        self.feature_branch()
        sha = commit(self.repo, "docs(x): leak a marker", {"README.md": "<!-- fork:begin -->\n"})
        code, out = run("mirror", "leak", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)


class TestFailClosed(ForkRepoTest):
    """A guard that cannot verify must not report success."""

    def setUp(self) -> None:
        super().setUp()
        self._gh_available = forkctl.gh_available
        self._gh_json = forkctl.gh_json

    def tearDown(self) -> None:
        forkctl.gh_available = self._gh_available
        forkctl.gh_json = self._gh_json
        super().tearDown()

    def test_doctor_fails_without_gh(self) -> None:
        forkctl.gh_available = lambda: False
        code, out = run("doctor")
        self.assertEqual(code, 1, out)

    def test_doctor_fails_when_gh_errors(self) -> None:
        forkctl.gh_available = lambda: True
        forkctl.gh_json = self._raise
        code, out = run("doctor")
        self.assertEqual(code, 1, out)
        self.assertIn("could not query", out)

    def test_status_fails_without_gh(self) -> None:
        forkctl.gh_available = lambda: False
        code, out = run("status", "--mirror", "mir", "--stack", "stack", "--upstream", "up")
        self.assertEqual(code, 1, out)

    def test_status_fails_when_upstream_is_not_fetched(self) -> None:
        code, out = run("status", "--mirror", "mir", "--stack", "stack", "--upstream", "nope")
        self.assertEqual(code, 1, out)

    def test_check_view_fails_without_gh(self) -> None:
        forkctl.gh_available = lambda: False
        code, out = run("check-view", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)

    def test_delete_mirror_fails_when_gh_errors(self) -> None:
        forkctl.gh_available = lambda: True
        forkctl.gh_json = self._raise
        code, out = run("delete-mirror", "slug")
        self.assertEqual(code, 1, out)
        self.assertIn("could not verify", out)

    def test_delete_mirror_refuses_an_open_pr_from_our_fork(self) -> None:
        forkctl.gh_available = lambda: True
        forkctl.gh_json = lambda *a: [
            {"number": 1, "state": "CLOSED", "headRepositoryOwner": {"login": "ASCS-eV"}},
            {"number": 2, "state": "OPEN", "headRepositoryOwner": {"login": "ASCS-eV"}},
        ]
        code, out = run("delete-mirror", "slug", "--dry-run")
        self.assertEqual(code, 1, out)
        self.assertIn("#2", out)

    def test_delete_mirror_ignores_another_fork_with_the_same_slug(self) -> None:
        forkctl.gh_available = lambda: True
        forkctl.gh_json = lambda *a: [
            {"number": 9, "state": "OPEN", "headRepositoryOwner": {"login": "SomeoneElse"}},
        ]
        code, out = run("delete-mirror", "slug", "--dry-run")
        self.assertEqual(code, 0, out)

    @staticmethod
    def _raise(*args: str):
        raise forkctl.ForkctlError("simulated gh outage")


class TestWorkflowParser(unittest.TestCase):
    def test_reads_inline_and_block_branch_lists(self) -> None:
        text = (
            "name: CI\non:\n  pull_request:\n    branches:\n      - 'main'\n      - 'integration/3.0'\n"
            "  push:\n    branches: ['main']\njobs: {}\n"
        )
        filters = forkctl.workflow_branch_filters(text)
        self.assertEqual(filters["pull_request"], ["main", "integration/3.0"])
        self.assertEqual(filters["push"], ["main"])

    def test_ignores_comments_and_handles_quoted_on(self) -> None:
        text = "'on':\n  pull_request:\n    # a comment\n    branches: ['integration/3.0']\n"
        self.assertEqual(forkctl.workflow_branch_filters(text)["pull_request"], ["integration/3.0"])

    def test_branches_ignore_is_a_different_key(self) -> None:
        """Prefix-matching `branches` let a commit swap in the opposite filter."""
        text = "on:\n  pull_request:\n    branches-ignore: ['integration/3.0']\n"
        self.assertEqual(forkctl.workflow_branch_filters(text)["pull_request"], [])

    def test_inline_comment_is_not_part_of_a_branch_name(self) -> None:
        text = "on:\n  pull_request:\n    branches: ['integration/3.0']  # fork CI\n"
        self.assertEqual(forkctl.workflow_branch_filters(text)["pull_request"], ["integration/3.0"])

    def test_a_block_scalar_decoy_is_rejected(self) -> None:
        text = "on:\n  pull_request:\n    description: |\n      branches: ['integration/3.0']\n"
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)

    def test_a_flow_mapping_is_rejected_rather_than_misread(self) -> None:
        text = "on:\n  pull_request: {branches: ['integration/3.0']}\n"
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)

    def test_an_alias_is_rejected(self) -> None:
        text = "on:\n  pull_request:\n    branches: *common\n"
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)

    def test_an_inline_on_value_is_rejected(self) -> None:
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters("on: [push, pull_request]\n")

    def test_a_tab_indent_is_rejected(self) -> None:
        text = "on:\n  pull_request:\n\t\tbranches: ['integration/3.0']\n"
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)


class TestRoundTwoRegressions(ForkRepoTest):
    """One per finding of the second adversarial review."""

    def write_ci(self, text: str) -> None:
        write(self.repo, ".github/workflows/ci.yml", text)

    def test_swapping_branches_for_branches_ignore_is_caught(self) -> None:
        self.branch_from_stack("feature/ignore-swap")
        swapped = CI_YML_FORKED.replace(
            "  pull_request:\n    branches: ['main', '3.0', 'integration/3.0']",
            "  pull_request:\n    branches-ignore: ['main', '3.0', 'integration/3.0']",
        )
        commit(self.repo, "ci(x): swap the filter", {".github/workflows/ci.yml": swapped})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("removed from `pull_request`", out)

    def test_unparseable_workflow_fails_closed(self) -> None:
        self.branch_from_stack("feature/unparseable")
        commit(
            self.repo,
            "ci(x): restructure triggers",
            {".github/workflows/ci.yml": "name: CI\non: [push, pull_request]\njobs: {}\n"},
        )
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)

    def test_a_later_stack_commit_touching_a_fork_only_file_is_caught(self) -> None:
        git(self.repo, "switch", "-q", "stack")
        commit(self.repo, "feat(x): sneak", {"CONTRIBUTING.md": "changed by a feature\n"})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("contaminated feature commit", out)

    def test_a_later_stack_commit_editing_the_fork_block_is_caught(self) -> None:
        git(self.repo, "switch", "-q", "stack")
        edited = AGENTS_FORKED.replace("fork process", "fork process, quietly rewritten")
        commit(self.repo, "docs(x): tweak", {"AGENTS.md": edited})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("contaminated feature commit", out)

    def test_a_shared_change_smuggled_into_the_fork_base_is_caught(self) -> None:
        git(self.repo, "switch", "-q", "-c", "stack-smuggle", "mir")
        tree = fork_base_tree()
        tree["AGENTS.md"] = "## Coding Standards\nSECRETLY CHANGED\n" + AGENTS_FORKED.split("\n", 2)[2]
        commit(self.repo, CHORE, tree)
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack-smuggle")
        self.assertEqual(code, 1, out)
        self.assertIn("smuggles a shared-file change", out)

    def test_a_fork_base_deleting_a_shared_workflow_is_caught(self) -> None:
        git(self.repo, "switch", "-q", "-c", "stack-del", "mir")
        tree = fork_base_tree()
        del tree[".github/workflows/ci.yml"]
        write(self.repo, ".github/copilot-instructions.md", "fork\n")
        for path, text in tree.items():
            write(self.repo, path, text)
        git(self.repo, "rm", "-q", ".github/workflows/ci.yml")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", CHORE)
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack-del")
        self.assertEqual(code, 1, out)
        self.assertIn("is missing", out)

    def test_gate_three_refuses_an_older_stack_commit(self) -> None:
        """--sha must agree with HEAD, or it certifies a diff nobody is merging."""
        self.branch_from_stack("feature/current")
        commit(self.repo, "feat(x): the commit actually under review", {"current.c": "x\n"})
        code, out = run(
            "mirror", "wrong", "--sha", self.feature, "--upstream", "up", "--mirror", "mir", "--stack", "stack"
        )
        self.assertEqual(code, 1, out)
        self.assertIn("is not HEAD", out)

    def test_gate_three_accepts_the_commit_under_review(self) -> None:
        self.branch_from_stack("feature/current-ok")
        sha = commit(self.repo, "feat(x): the commit actually under review", {"current.c": "x\n"})
        code, out = run("mirror", "ok", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 0, out)

    def test_an_invalid_slug_is_refused_before_anything_moves(self) -> None:
        sha = self.stacked_feature("feat(x): work under review", {"slug.c": "x\n"})
        code, out = run("mirror", "bad..slug", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("invalid slug", out)
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "feature/x")

    def test_an_exception_after_checkout_still_restores_the_branch(self) -> None:
        sha = self.stacked_feature("feat(x): work under review", {"boom.c": "x\n"})
        original = forkctl.fork_markers_anywhere
        forkctl.fork_markers_anywhere = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                run("mirror", "boom", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        finally:
            forkctl.fork_markers_anywhere = original
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "feature/x")
        self.assertNotIn("mirror/boom", git(self.repo, "branch", "--list", "mirror/boom"))

    def test_prose_under_github_may_mention_the_fork_branch(self) -> None:
        """Scoping the marker to all of .github/ blocked legitimate documentation."""
        self.branch_from_stack("feature/prose")
        sha = commit(
            self.repo,
            "docs(x): document branch naming",
            {".github/branch-naming.md": "An example long-lived branch is integration/3.0.\n"},
        )
        code, out = run("mirror", "prose", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 0, out)

    def test_a_workflow_mentioning_the_fork_branch_is_still_caught(self) -> None:
        self.branch_from_stack("feature/wf")
        sha = commit(
            self.repo,
            "ci(x): add a workflow",
            {".github/workflows/extra.yml": "on:\n  push:\n    branches: ['integration/3.0']\n"},
        )
        code, out = run("mirror", "wf", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("fork-only marker", out)

    def test_delete_mirror_reports_a_failed_local_delete(self) -> None:
        git(self.repo, "switch", "-q", "-c", "mirror/busy", "up")
        saved = (forkctl.gh_available, forkctl.gh_json, forkctl.git_rc, forkctl.git_lines)
        original_rc = forkctl.git_rc

        def fake_rc(*args: str) -> int:
            # The remote delete succeeds; the local one cannot, because the
            # branch is checked out. The command must not call that success.
            return 0 if args[:3] == ("push", "origin", "--delete") else original_rc(*args)

        forkctl.gh_available = lambda: True
        forkctl.gh_json = lambda *a, **k: []
        forkctl.git_rc = fake_rc
        forkctl.git_lines = lambda *a, **k: []
        try:
            code, out = run("delete-mirror", "busy")
        finally:
            forkctl.gh_available, forkctl.gh_json, forkctl.git_rc, forkctl.git_lines = saved
        self.assertEqual(code, 1, out)
        self.assertIn("could not be deleted", out)


class TestRoundThreeRegressions(ForkRepoTest):
    """One per finding of the third adversarial review."""

    def test_a_negative_pattern_is_rejected(self) -> None:
        """GitHub evaluates patterns in order, so a later `!` excludes the branch."""
        text = "on:\n  pull_request:\n    branches: ['main', 'integration/3.0', '!integration/3.0']\n"
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)

    def test_a_negative_pattern_in_a_block_list_is_rejected(self) -> None:
        text = "on:\n  pull_request:\n    branches:\n      - 'integration/3.0'\n      - '!integration/3.0'\n"
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)

    def test_a_comma_inside_a_quoted_scalar_is_one_branch(self) -> None:
        text = "on:\n  pull_request:\n    branches: ['main', '3.0,integration/3.0']\n"
        values = forkctl.workflow_branch_filters(text)["pull_request"]
        self.assertEqual(values, ["main", "3.0,integration/3.0"])
        self.assertNotIn("integration/3.0", values)

    def test_a_boolean_on_key_is_not_the_on_block(self) -> None:
        """YAML 1.1 coerces bare `on` to true, but GitHub reads only `on`."""
        text = "True:\n  pull_request:\n    branches: ['integration/3.0']\n"
        self.assertEqual(forkctl.workflow_branch_filters(text), {})

    def test_branches_nested_under_another_key_is_not_the_filter(self) -> None:
        text = "on:\n  pull_request:\n    types:\n      branches: ['integration/3.0']\n"
        self.assertEqual(forkctl.workflow_branch_filters(text)["pull_request"], [])

    def test_duplicate_branches_keys_are_rejected(self) -> None:
        text = "on:\n  pull_request:\n    branches: ['integration/3.0']\n    branches: ['main']\n"
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)

    def test_duplicate_triggers_are_rejected(self) -> None:
        text = "on:\n  pull_request:\n    branches: ['integration/3.0']\n  pull_request:\n    branches: ['main']\n"
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)

    def test_an_excluded_branch_is_caught_end_to_end(self) -> None:
        self.branch_from_stack("feature/negated")
        negated = CI_YML_FORKED.replace(
            "branches: ['main', '3.0', 'integration/3.0']",
            "branches: ['main', '3.0', 'integration/3.0', '!integration/3.0']",
        )
        commit(self.repo, "ci(x): exclude the fork branch", {".github/workflows/ci.yml": negated})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)

    def test_a_deleted_required_fork_file_is_caught(self) -> None:
        git(self.repo, "switch", "-q", "-c", "stack-no-detector", "mir")
        tree = fork_base_tree()
        del tree[".github/workflows/fork-only-view.yml"]
        for path, text in tree.items():
            write(self.repo, path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", CHORE)
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack-no-detector")
        self.assertEqual(code, 1, out)
        self.assertIn("required fork-only infrastructure was deleted", out)

    def test_the_well_formed_fixture_carries_every_required_file(self) -> None:
        """Guards the test above: an incomplete fixture would hide the invariant."""
        for path in forkctl.REQUIRED_FORK_FILES:
            self.assertIn(path, fork_base_tree(), path)

    def test_a_feature_repairing_a_malformed_base_marker_is_caught(self) -> None:
        git(self.repo, "switch", "-q", "-c", "stack-stray", "mir")
        tree = fork_base_tree()
        tree["AGENTS.md"] = AGENTS_FORKED + "<!-- fork:end -->\n"
        for path, text in tree.items():
            write(self.repo, path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", CHORE)
        commit(self.repo, "docs(x): tidy up", {"AGENTS.md": AGENTS_FORKED})
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack-stray")
        self.assertEqual(code, 1, out)
        self.assertIn("contaminated feature commit", out)

    def test_a_malformed_fork_base_is_caught_on_its_own(self) -> None:
        git(self.repo, "switch", "-q", "-c", "stack-stray-only", "mir")
        tree = fork_base_tree()
        tree["AGENTS.md"] = AGENTS_FORKED + "<!-- fork:end -->\n"
        for path, text in tree.items():
            write(self.repo, path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", CHORE)
        code, out = run("check-stack", "--mirror", "mir", "--stack", "stack-stray-only")
        self.assertEqual(code, 1, out)
        self.assertIn("marker structure is broken", out)

    def test_gate_three_refuses_a_detached_head(self) -> None:
        """A detached HEAD at an older stack commit certified a diff nobody merges."""
        git(self.repo, "switch", "-q", "stack")
        older = git(self.repo, "rev-parse", "HEAD")
        commit(self.repo, "feat(x): newer stacked work", {"newer.c": "x\n"})
        git(self.repo, "switch", "-q", "--detach", older)
        code, out = run("mirror", "old", "--sha", older, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("HEAD is detached", out)

    def test_gate_three_refuses_a_branch_that_is_not_one_commit_on_the_tip(self) -> None:
        git(self.repo, "switch", "-q", "-c", "feature/two", "stack")
        commit(self.repo, "feat(x): first", {"a.c": "x\n"})
        sha = commit(self.repo, "feat(x): second", {"b.c": "x\n"})
        code, out = run("mirror", "two", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("not a single commit on top of", out)

    def test_an_interrupted_cherry_pick_is_aborted_and_restored(self) -> None:
        """Ctrl-C between the cherry-pick and its abort left a conflicted detached HEAD."""
        git(self.repo, "switch", "-q", "up")
        commit(self.repo, "upstream: conflicting edit", {"sim.c": "UPSTREAM\n"})
        sha = self.stacked_feature("feat(x): conflicting work", {"sim.c": "OURS\n"}, "feature/interrupted")
        saved = forkctl.git_rc

        def interrupt_after_cherry_pick(*argv: str) -> int:
            code = saved(*argv)
            if argv[0] == "cherry-pick" and "--abort" not in argv:
                raise KeyboardInterrupt
            return code

        forkctl.git_rc = interrupt_after_cherry_pick
        try:
            with self.assertRaises(KeyboardInterrupt):
                run("mirror", "boom", "--sha", sha, "--upstream", "up", "--mirror", "mir", "--stack", "stack")
        finally:
            forkctl.git_rc = saved
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "feature/interrupted")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_a_crlf_rewrite_in_the_fork_base_is_not_normalised_away(self) -> None:
        clean = AGENTS_UPSTREAM + "\n"
        self.assertNotEqual(
            forkctl.strip_fork_block(AGENTS_FORKED.replace("\n", "\r\n")),
            forkctl.strip_fork_block(AGENTS_FORKED),
        )
        self.assertEqual(forkctl.strip_fork_block(AGENTS_FORKED), clean)

    def test_the_real_fork_base_shape_still_compares_equal(self) -> None:
        """The strip is exact, so the legitimate base must still pass it."""
        self.assertIn(
            forkctl.strip_fork_block(AGENTS_UPSTREAM),
            (forkctl.strip_fork_block(AGENTS_FORKED), forkctl.strip_fork_block(AGENTS_FORKED).rstrip("\n") + "\n"),
        )


class TestRoundFourRegressions(ForkRepoTest):
    def test_a_duplicate_top_level_on_key_is_refused(self) -> None:
        """Only the first `on:` was read, so a second one could remove the PR trigger."""
        text = (
            "on:\n  pull_request:\n    branches: [main, '3.0', 'integration/3.0']\n"
            "\non:\n  push:\n    branches: [main]\n"
        )
        with self.assertRaises(forkctl.ForkctlError):
            forkctl.workflow_branch_filters(text)

    def test_a_duplicate_on_key_fails_check_commit_closed(self) -> None:
        self.branch_from_stack("feature/dup-on")
        commit(
            self.repo,
            "ci(x): workflow with two on: keys",
            {
                ".github/workflows/ci.yml": (
                    "on:\n  pull_request:\n    branches: [main, '3.0', 'integration/3.0']\n"
                    "\non:\n  push:\n    branches: [main]\n"
                )
            },
        )
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)

    def test_moving_an_intact_fork_block_is_caught(self) -> None:
        """Git renders the move as relocating the upstream text, so no marker shows in the diff."""
        moved = "\n<!-- fork:begin -->\nfork process\n<!-- fork:end -->\n" + AGENTS_UPSTREAM
        self.branch_from_stack("feature/moved-block")
        commit(self.repo, "docs(x): tidy AGENTS.md", {"AGENTS.md": moved})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 1, out)
        self.assertIn("must end the file", out)

    def test_a_moved_block_leaves_the_marker_text_identical(self) -> None:
        """Why a position rule is needed: comparing marker text cannot see a move."""
        moved = "\n<!-- fork:begin -->\nfork process\n<!-- fork:end -->\n" + AGENTS_UPSTREAM
        self.branch_from_stack("feature/moved-probe")
        sha = commit(self.repo, "docs(x): tidy AGENTS.md", {"AGENTS.md": moved})
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            self.assertEqual(
                forkctl.fork_marker_lines(f"{sha}~1", "AGENTS.md"), forkctl.fork_marker_lines(sha, "AGENTS.md")
            )
            self.assertEqual(forkctl.changed_fork_markers(f"{sha}~1", sha), [])
            self.assertIn("AGENTS.md", forkctl.malformed_fork_blocks(sha)[0])
        finally:
            os.chdir(cwd)

    def test_an_edit_above_the_fork_block_is_still_allowed(self) -> None:
        """The position rule must not fire on legitimate upstream-side edits."""
        edited = AGENTS_UPSTREAM + "a new paragraph\n\n<!-- fork:begin -->\nfork process\n<!-- fork:end -->\n"
        self.branch_from_stack("feature/edit-above")
        commit(self.repo, "docs(x): extend AGENTS.md", {"AGENTS.md": edited})
        code, out = run("check-commit", "--base", "stack")
        self.assertEqual(code, 0, out)

    def test_a_crlf_fork_base_separator_is_accepted(self) -> None:
        """The separator must follow the file's own line endings, not hard-coded LF."""
        upstream = AGENTS_UPSTREAM.replace("\n", "\r\n")
        forked = AGENTS_FORKED.replace("\n", "\r\n")
        stripped = forkctl.strip_fork_block(forked)
        separator = "\r\n" if "\r\n" in upstream else "\n"
        self.assertIn(stripped, (upstream, upstream + separator))


class TestPureHelpers(unittest.TestCase):
    def test_fork_only_paths_are_matched_exactly(self) -> None:
        self.assertTrue(forkctl.is_fork_only_path("CONTRIBUTING.md"))
        self.assertTrue(forkctl.is_fork_only_path(".github/fork/anything.py"))
        self.assertFalse(forkctl.is_fork_only_path("docs/CONTRIBUTING.md"))
        self.assertFalse(forkctl.is_fork_only_path("pufferlib/ocean/drive/drive.h"))

    def test_substring_lookalikes_are_not_fork_only(self) -> None:
        """The shell guards this replaces matched substrings and over-fired."""
        self.assertFalse(forkctl.is_fork_only_path("docs/sync-mirror-notes.md"))
        self.assertFalse(forkctl.is_fork_only_path("tools/pull_request_template.py"))

    def test_diff_headers_are_not_treated_as_content(self) -> None:
        diff = (
            "diff --git a/AGENTS.md b/AGENTS.md\n--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1 +1 @@\n-old line\n+new line\n"
        )
        self.assertEqual(forkctl.diff_content_lines(diff), ["-old line", "+new line"])
        self.assertEqual(forkctl.diff_content_lines(diff, added_only=True), ["+new line"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
