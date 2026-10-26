#!/usr/bin/env python3
"""Fork-process checks for ASCS-eV/PufferDrive.

Fork-only tooling. Never cherry-pick this file upstream.

This is the single implementation of the guards described in
`.github/copilot-instructions.md`. Humans, coding agents and CI all run this
same code instead of retyping shell snippets, so a guard cannot be mistyped at
one call site and silently pass at another.

It is deliberately stdlib-only and shell-free. The fork process depends on these
checks failing *loudly*, and the classic ways a shell guard dies quietly have no
analogue here: `grep -c` exiting 1 on no match, `git log | grep -q` returning
141 under `pipefail`, a pipeline's exit status being swallowed.

Every subcommand exits 0 on success and 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys


MIRROR_REF = "origin/3.0"
STACK_REF = "origin/integration/3.0"
UPSTREAM_REF = "upstream/3.0"
VIEW_BRANCH = "fork-only/3.0"

FORK_REPO = "ASCS-eV/PufferDrive"
UPSTREAM_REPO = "Emerge-Lab/PufferDrive"
UPSTREAM_URL = "https://github.com/Emerge-Lab/PufferDrive.git"
FORK_OWNER = "ASCS-eV"

FORK_COMMIT_PREFIX = "chore(fork): "

# Files that exist only in this fork; none may appear in a feature diff. This
# tuple is the single source of truth. The docs point here instead of repeating
# the list, so adding a fork-only file cannot leave a stale copy behind.
FORK_ONLY_PATHS = (
    ".github/copilot-instructions.md",
    "CONTRIBUTING.md",
    ".github/pull_request_template.md",
    ".github/workflows/sync-mirror.yml",
    ".github/workflows/fork-only-view.yml",
)

# Anything under these prefixes is fork-only by construction, so a new fork-only
# tool dropped into `.github/fork/` is covered without editing this file.
FORK_ONLY_PREFIXES = (".github/fork/",)

# Registration is only half the invariant. These files must also *exist*: a base
# rewrite that quietly drops the detector or the always-loaded instructions would
# otherwise pass every check, because nothing was touched that is unregistered.
REQUIRED_FORK_FILES = FORK_ONLY_PATHS + (
    ".github/fork/forkctl.py",
    ".github/fork/test_forkctl.py",
    ".github/fork/PROCESS.md",
)

# Shared with upstream, but carrying a fork-only hunk. The file may legitimately
# appear in a feature diff; only those hunks must stay untouched.
SHARED_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    ".github/workflows/ci.yml",
    ".github/workflows/install.yml",
)

FORK_HUNK_MARKERS = ("integration/3.0", "fork:begin", "fork:end")

# `integration/3.0` is only evidence of fork-only content inside workflow
# configuration. Elsewhere -- including prose under `.github/` -- it is an
# ordinary string an upstream-bound commit may legitimately write.
FORK_PATH_SCOPED_MARKERS = {"integration/3.0": (".github/workflows/",)}

# Workflows shared with upstream must keep the fork branch in the `pull_request`
# trigger and nowhere else. Moving it to `push:` leaves the marker line untouched,
# so only a structural read of the trigger tree catches it.
FORK_WORKFLOW_FILES = (".github/workflows/ci.yml", ".github/workflows/install.yml")
FORK_WORKFLOW_BRANCH = "integration/3.0"
FORK_WORKFLOW_TRIGGER = "pull_request"

CONVENTIONAL_TYPES = (
    "feat",
    "fix",
    "perf",
    "refactor",
    "test",
    "docs",
    "build",
    "ci",
    "chore",
)
CONVENTIONAL_RE = re.compile(rf"^({'|'.join(CONVENTIONAL_TYPES)})(\([a-z0-9][a-z0-9-]*\))?!?: \S.*$")
MAX_SUBJECT_LENGTH = 72

# Files whose fork-only part is a delimited block rather than a single line.
# Comparing the block's whole content catches an edit *between* the markers,
# which a diff-line scan misses: those changed lines carry no marker themselves.
FORK_BLOCK_FILES = ("AGENTS.md", "CLAUDE.md")
FORK_BLOCK_BEGIN = "fork:begin"
FORK_BLOCK_END = "fork:end"

TERMINAL_PR_STATES = ("MERGED", "CLOSED", "NONE")


class ForkctlError(RuntimeError):
    pass


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "--no-pager", *args], capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise ForkctlError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    # Strip line terminators only. Stripping arbitrary whitespace would silently
    # normalise a subject like " chore(fork): x" into a conforming one.
    return proc.stdout.strip("\r\n")


def git_rc(*args: str) -> int:
    proc = subprocess.run(["git", "--no-pager", *args], capture_output=True, text=True, check=False)
    return proc.returncode


def show_file(ref: str, path: str) -> str | None:
    """Contents of `path` at `ref`, or None if it genuinely does not exist.

    `git show` with check=False conflates "absent", "empty" and "git failed",
    and every caller then reads the empty string as "nothing to check". Probing
    for existence first keeps a git error loud instead of fail-open.

    Returned verbatim, including trailing newlines: `git()` strips line
    terminators, which would hide a trailing-newline rewrite from the byte
    comparison in `smuggled_shared_changes()`.
    """
    if git_rc("cat-file", "-e", f"{ref}:{path}") != 0:
        return None
    proc = subprocess.run(["git", "--no-pager", "show", f"{ref}:{path}"], capture_output=True, check=False)
    if proc.returncode != 0:
        raise ForkctlError(f"git show {ref}:{path} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8")


def head_is_detached() -> bool:
    return git_rc("symbolic-ref", "--quiet", "HEAD") != 0


def git_lines(*args: str) -> list[str]:
    out = git(*args)
    return out.splitlines() if out else []


def gh_available() -> bool:
    return shutil.which("gh") is not None


def gh_json(*args: str):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ForkctlError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout or "[]")


class Report:
    """Collects check outcomes and renders them for a terminal or for CI."""

    def __init__(self, title: str, fmt: str = "text", stream=None) -> None:
        self.title = title
        self.fmt = fmt
        self.stream = stream if stream is not None else sys.stdout
        self.failed = False
        if fmt == "text":
            print(f"== {title}", file=self.stream)

    def ok(self, message: str) -> None:
        if self.fmt == "text":
            print(f"  PASS  {message}", file=self.stream)

    def info(self, message: str) -> None:
        if self.fmt == "text":
            print(f"        {message}", file=self.stream)

    def warn(self, message: str) -> None:
        if self.fmt == "github":
            print(f"::warning::{message}", file=self.stream)
        else:
            print(f"  WARN  {message}", file=self.stream)

    def fail(self, message: str, fix: str | None = None) -> None:
        self.failed = True
        if self.fmt == "github":
            print(f"::error::{message}" + (f" Fix: {fix}" if fix else ""), file=self.stream)
        else:
            print(f"  FAIL  {message}", file=self.stream)
            if fix:
                print(f"        fix: {fix}", file=self.stream)

    def check(self, passed: bool, ok: str, bad: str, fix: str | None = None) -> bool:
        if passed:
            self.ok(ok)
            return True
        self.fail(bad, fix)
        return False

    def finish(self) -> int:
        if self.fmt == "text":
            print(f"== {self.title}: {'FAILED' if self.failed else 'OK'}", file=self.stream)
        return 1 if self.failed else 0


def ref_exists(ref: str) -> bool:
    return git_rc("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") == 0


def require_refs(report: Report, *refs: str) -> bool:
    missing = [ref for ref in refs if not ref_exists(ref)]
    if not missing:
        return True
    report.fail(
        f"ref(s) not found locally: {', '.join(missing)}",
        "git fetch origin && git fetch upstream",
    )
    return False


def is_fork_only_path(path: str) -> bool:
    return path in FORK_ONLY_PATHS or path.startswith(FORK_ONLY_PREFIXES)


def diff_content_lines(diff: str, added_only: bool = False) -> list[str]:
    """Content lines of a diff, excluding the `+++`/`---` file headers."""
    wanted = ("+",) if added_only else ("+", "-")
    return [line for line in diff.splitlines() if line.startswith(wanted) and not line.startswith(("+++", "---"))]


def fork_only_files_in(base: str, head: str = "HEAD") -> list[str]:
    # --no-renames: with rename detection on, `git diff --name-only` reports only
    # the destination, so renaming a fork-only file out of the way would hide it.
    files = git_lines("diff", "--no-renames", "--name-only", f"{base}...{head}")
    return [path for path in files if is_fork_only_path(path)]


def diff_files(base: str, head: str = "HEAD") -> list[str]:
    return git_lines("diff", "--no-renames", "--name-only", f"{base}...{head}")


def marker_applies_to(marker: str, path: str) -> bool:
    scopes = FORK_PATH_SCOPED_MARKERS.get(marker)
    return True if scopes is None else path.startswith(scopes)


def fork_markers_anywhere(base: str, head: str = "HEAD") -> list[str]:
    """Added lines referencing fork-only state, across the whole diff.

    Used against pure upstream, where no added line should mention the fork.
    Path-scoped markers are only evidence inside the paths they are scoped to,
    so ordinary prose containing the branch name does not block a contribution.
    """
    offenders = []
    for path in diff_files(base, head):
        markers = [marker for marker in FORK_HUNK_MARKERS if marker_applies_to(marker, path)]
        if not markers:
            continue
        diff = git("diff", f"{base}...{head}", "--", path)
        offenders += [
            f"{path}: {line.strip()}"
            for line in diff_content_lines(diff, added_only=True)
            if any(marker in line for marker in markers)
        ]
    return offenders


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing `# ...` comment that is not inside a quoted scalar."""
    out: list[str] = []
    quote = None
    for index, char in enumerate(value):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or value[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out).rstrip()


def _reject_unsupported_yaml(line: str) -> None:
    """Fail closed on YAML this reader is not equipped to understand.

    A hand-rolled indentation reader that silently mis-parses is worse than no
    reader at all: it reports PASS on a file GitHub reads differently. Anything
    outside the small subset these workflows actually use is an error.
    """
    if "\t" in line[: len(line) - len(line.lstrip())]:
        raise ForkctlError(f"tab in workflow indentation, which YAML forbids: {line!r}")
    _, _, value = line.partition(":")
    value = _strip_inline_comment(value).strip()
    if value[:1] in ("&", "*"):
        raise ForkctlError(f"YAML anchor/alias in the `on:` block is not supported: {line!r}")
    if value[:1] in ("|", ">"):
        raise ForkctlError(f"YAML block scalar in the `on:` block is not supported: {line!r}")
    if value.startswith("{"):
        raise ForkctlError(f"YAML flow mapping in the `on:` block is not supported: {line!r}")
    if line.lstrip().startswith("<<"):
        raise ForkctlError(f"YAML merge key in the `on:` block is not supported: {line!r}")


def workflow_branch_filters(text: str) -> dict[str, list[str]]:
    """Map trigger -> branches under `on.<trigger>.branches` of a workflow.

    A deliberately small indentation reader rather than a YAML dependency: this
    script must stay stdlib-only, and it only needs one well-known subtree. It
    raises rather than guessing whenever the input leaves that subset, and it
    matches the `branches` key exactly -- `branches-ignore` is a different key
    with the opposite meaning, so treating it as a prefix match let a commit
    swap one for the other and keep the marker line intact.
    """
    raw = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    lines = [_strip_inline_comment(line) for line in raw]
    lines = [line for line in lines if line.strip()]
    filters: dict[str, list[str]] = {}

    on_keys = [i for i, line in enumerate(lines) if re.match(r"^(on|'on'|\"on\"):", line)]
    if len(on_keys) > 1:
        raise ForkctlError(f"workflow declares `on:` {len(on_keys)} times; which triggers apply is ambiguous")
    if not on_keys:
        return filters
    on_index = on_keys[0]
    _reject_unsupported_yaml(lines[on_index])
    if lines[on_index].partition(":")[2].strip():
        raise ForkctlError(f"inline `on:` value is not supported: {lines[on_index]!r}")

    body = []
    for line in lines[on_index + 1 :]:
        if indent_of(line) == 0:
            break
        _reject_unsupported_yaml(line)
        body.append(line)
    if not body:
        return filters

    trigger_indent = min(indent_of(line) for line in body)
    for start, trigger in _trigger_blocks(body, trigger_indent):
        filters.setdefault(trigger, [])
        block = _child_lines(body, start, trigger_indent)
        if not block:
            continue
        child_indent = min(indent_of(line) for line in block)
        # `branches` counts only as a direct child of the trigger. Accepting it
        # at any deeper indent let it be read out of an unrelated nested key.
        hits = [
            index
            for index, line in enumerate(block)
            if indent_of(line) == child_indent and line.strip().partition(":")[0].strip().strip("'\"") == "branches"
        ]
        if len(hits) > 1:
            raise ForkctlError(f"duplicate `branches:` key under `{trigger}:`")
        if hits:
            filters[trigger] = _branch_values(block, hits[0])
    return filters


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _trigger_blocks(body: list[str], trigger_indent: int) -> list[tuple[int, str]]:
    """`(index, name)` of every trigger key in the `on:` block."""
    seen: set[str] = set()
    blocks = []
    for index, line in enumerate(body):
        if indent_of(line) != trigger_indent:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            blocks.append((index, stripped[2:].strip().strip("'\"")))
            continue
        if not stripped.endswith(":"):
            raise ForkctlError(f"unrecognised entry in the `on:` block: {line!r}")
        name = stripped.rstrip(":").strip().strip("'\"")
        if name in seen:
            raise ForkctlError(f"duplicate `{name}:` trigger in the `on:` block")
        seen.add(name)
        blocks.append((index, name))
    return blocks


def _child_lines(body: list[str], start: int, trigger_indent: int) -> list[str]:
    block = []
    for line in body[start + 1 :]:
        if indent_of(line) <= trigger_indent:
            break
        block.append(line)
    return block


def _split_flow_list(inline: str) -> list[str]:
    """Split `[a, 'b,c']` on commas that are not inside a quoted scalar."""
    items, current, quote = [], [], None
    for char in inline:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            continue
        if char == ",":
            items.append("".join(current))
            current = []
            continue
        current.append(char)
    if quote:
        raise ForkctlError(f"unterminated quote in a `branches:` list: {inline!r}")
    items.append("".join(current))
    return [item for item in (item.strip() for item in items) if item]


def _unquote_branch(value: str) -> str:
    """One `branches:` entry, with its quotes removed and its shape checked."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    elif value[:1] in ("'", '"') or value[-1:] in ("'", '"'):
        raise ForkctlError(f"unbalanced quotes in a `branches:` entry: {value!r}")
    if value.startswith("!"):
        # GitHub evaluates patterns in order, so a later `!` silently excludes a
        # branch this reader would still report as listed.
        raise ForkctlError(f"negative branch pattern is not supported: {value!r}")
    return value


def _branch_values(body: list[str], index: int) -> list[str]:
    """Values of a `branches:` key, inline (`[a, b]`) or as a block list."""
    line = body[index]
    indent = indent_of(line)
    _, _, inline = line.partition(":")
    inline = inline.strip()
    if inline:
        if not (inline.startswith("[") and inline.endswith("]")):
            raise ForkctlError(f"unrecognised inline `branches:` value: {line!r}")
        return [_unquote_branch(item) for item in _split_flow_list(inline[1:-1])]

    values = []
    for following in body[index + 1 :]:
        if indent_of(following) <= indent:
            break
        if not following.strip().startswith("- "):
            raise ForkctlError(f"unrecognised entry under `branches:`: {following!r}")
        values.append(_unquote_branch(following.strip()[2:].strip()))
    return values


def fork_workflow_triggers(ref: str, path: str) -> list[str]:
    """Triggers whose branch filter lists the fork branch, at `ref`."""
    text = show_file(ref, path)
    if text is None:
        return []
    return sorted(
        trigger for trigger, branches in workflow_branch_filters(text).items() if FORK_WORKFLOW_BRANCH in branches
    )


def fork_workflow_problems(base: str, head: str = "HEAD") -> list[str]:
    """Section 3.2: the fork branch filter must stay put, in `pull_request` only."""
    merge_base = git("merge-base", base, head)
    problems = []
    for path in FORK_WORKFLOW_FILES:
        before = fork_workflow_triggers(merge_base, path)
        after = fork_workflow_triggers(head, path)
        if before == after:
            continue
        if FORK_WORKFLOW_TRIGGER in before and FORK_WORKFLOW_TRIGGER not in after:
            problems.append(f"{path}: the fork branch filter was removed from `{FORK_WORKFLOW_TRIGGER}`")
        for trigger in sorted(set(after) - set(before)):
            problems.append(f"{path}: the fork branch filter was added to `{trigger}`")
    return problems


def fork_blocks(ref: str, path: str) -> list[str]:
    """Every `fork:begin`...`fork:end` block of `path` at `ref`, in order."""
    content = show_file(ref, path)
    if content is None:
        return []
    blocks, start = [], None
    for index, line in enumerate(content.splitlines()):
        if FORK_BLOCK_BEGIN in line:
            start = index if start is None else start
        elif FORK_BLOCK_END in line and start is not None:
            blocks.append("\n".join(content.splitlines()[start : index + 1]))
            start = None
    return blocks


def malformed_fork_blocks(ref: str) -> list[str]:
    """Shared files whose marker structure is not exactly one trailing block."""
    problems = []
    for path in FORK_BLOCK_FILES:
        content = show_file(ref, path)
        if content is None:
            continue
        lines = content.splitlines()
        begins = [i for i, line in enumerate(lines) if FORK_BLOCK_BEGIN in line]
        ends = [i for i, line in enumerate(lines) if FORK_BLOCK_END in line]
        if len(begins) != 1 or len(ends) != 1 or begins[0] > ends[0]:
            problems.append(
                f"{path}: {len(begins)} fork:begin / {len(ends)} fork:end marker(s), expected one ordered pair"
            )
            continue
        # The block is appended to upstream content, so nothing may follow it.
        # Position, not content, is what a relocation changes, and git is free
        # to render a move as relocating the *other* side of the file.
        if any(line.strip() for line in lines[ends[0] + 1 :]):
            problems.append(f"{path}: content follows the fork:end marker; the fork block must end the file")
    return problems


def changed_fork_blocks(base: str, head: str = "HEAD") -> list[str]:
    merge_base = git("merge-base", base, head)
    return [path for path in FORK_BLOCK_FILES if fork_blocks(merge_base, path) != fork_blocks(head, path)]


def fork_marker_lines(ref: str, path: str) -> list[str]:
    """Every marker line of `path` at `ref`, whether or not it forms a block.

    `fork_blocks()` reports only completed pairs, so a stray marker is invisible
    to it -- which let a feature commit tidy up a malformed base and have the
    change counted as upstream-clean.
    """
    content = show_file(ref, path)
    if content is None:
        return []
    return [line for line in content.splitlines() if FORK_BLOCK_BEGIN in line or FORK_BLOCK_END in line]


def changed_fork_markers(base: str, head: str) -> list[str]:
    return [path for path in FORK_BLOCK_FILES if fork_marker_lines(base, path) != fork_marker_lines(head, path)]


def fork_state_problems(ref: str) -> list[str]:
    """The fork-only state every stack ref must have, stated absolutely.

    A before/after comparison only catches a change made by the commit under
    review. It says nothing about a stack that was already wrong, so the end
    state is asserted here rather than inferred from a diff.
    """
    problems = []
    for path in REQUIRED_FORK_FILES:
        if show_file(ref, path) is None:
            problems.append(f"{path} is missing; required fork-only infrastructure was deleted")
    for path in FORK_WORKFLOW_FILES:
        if show_file(ref, path) is None:
            problems.append(f"{path} is missing; a shared workflow was deleted")
            continue
        triggers = fork_workflow_triggers(ref, path)
        if triggers != [FORK_WORKFLOW_TRIGGER]:
            found = ", ".join(triggers) if triggers else "no trigger"
            problems.append(f"{path}: fork branch filter is on `{found}`, expected `{FORK_WORKFLOW_TRIGGER}` only")
    for path in FORK_BLOCK_FILES:
        if show_file(ref, path) is None:
            problems.append(f"{path} is missing; a shared file carrying a fork block was deleted")
            continue
        blocks = fork_blocks(ref, path)
        if len(blocks) != 1:
            problems.append(f"{path}: {len(blocks)} fork-only block(s), expected exactly 1")
    return problems


def strip_fork_block(text: str) -> str:
    """`text` with every fork:begin..fork:end block removed.

    Line endings are preserved: normalising them would let a CRLF/LF rewrite of
    a shared file compare equal to its upstream parent and ride along in the one
    commit that is exempt from Gate 3.
    """
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        if FORK_BLOCK_BEGIN in line:
            inside = True
            continue
        if FORK_BLOCK_END in line:
            inside = False
            continue
        if not inside:
            out.append(line)
    return "".join(out)


def strip_fork_branch(text: str) -> str:
    """`text` with the fork branch removed from every `branches:` list."""
    pattern = re.compile(rf",\s*['\"]?{re.escape(FORK_WORKFLOW_BRANCH)}['\"]?")
    out = []
    for line in text.splitlines(keepends=True):
        if "branches" in line and FORK_WORKFLOW_BRANCH in line:
            line = pattern.sub("", line)
        out.append(line)
    return "".join(out)


def smuggled_shared_changes(parent: str, base: str) -> list[str]:
    """Shared-file edits in the fork base that are not the fork-only hunk.

    The fork base is allowed to touch shared files -- adding the fork block and
    the branch filter is its job -- so a file-name check cannot police it. Each
    shared file is therefore compared with its upstream parent after the
    fork-only hunk is removed from both; anything left over is a change that
    was folded into the base commit where no guard would ever look at it.
    """
    problems = []
    for path in SHARED_FILES:
        before, after = show_file(parent, path), show_file(base, path)
        if before is None and after is None:
            continue
        if after is None:
            problems.append(f"{path} was deleted by the fork base")
            continue
        if before is None:
            problems.append(f"{path} is introduced by the fork base but is registered as shared with upstream")
            continue
        strip = strip_fork_block if path in FORK_BLOCK_FILES else strip_fork_branch
        # The fork block is appended after a blank separator line, so removing it
        # leaves exactly that one extra newline. Nothing else -- not a trailing
        # newline, not a CRLF rewrite -- may differ.
        upstream = strip(before)
        separator = "\r\n" if "\r\n" in upstream else "\n"
        allowed = (upstream, upstream + separator) if path in FORK_BLOCK_FILES else (upstream,)
        if strip(after) not in allowed:
            problems.append(f"{path} carries a non-fork-only change in the fork base")
    return problems


def subject_problems(subject: str) -> list[str]:
    problems = []
    if subject != subject.strip():
        problems.append("has leading or trailing whitespace")
    if not CONVENTIONAL_RE.match(subject):
        problems.append(f"is not Conventional Commits ({'|'.join(CONVENTIONAL_TYPES)})(scope): summary")
    if len(subject) > MAX_SUBJECT_LENGTH:
        problems.append(f"is {len(subject)} chars, over the {MAX_SUBJECT_LENGTH}-char limit")
    return problems


def working_tree_clean() -> bool:
    return git("status", "--porcelain") == ""


def patch_id(rev: str) -> str | None:
    """Content identity of a commit, ignoring its message and its parents."""
    diff = subprocess.run(
        ["git", "--no-pager", "diff", f"{rev}^", rev],
        check=False,
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0 or not diff.stdout:
        return None
    proc = subprocess.run(
        ["git", "patch-id", "--stable"],
        check=False,
        input=diff.stdout,
        capture_output=True,
        text=True,
    )
    return proc.stdout.split()[0] if proc.stdout.strip() else None


def mirror_ref(slug: str) -> str | None:
    for candidate in (f"origin/mirror/{slug}", f"mirror/{slug}"):
        if ref_exists(candidate):
            return candidate
    return None


def merges_cleanly(ref: str, onto: str) -> bool:
    """Whether ref still applies to onto, without touching the working tree."""
    proc = subprocess.run(
        ["git", "--no-pager", "merge-tree", "--write-tree", onto, ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        # Git < 2.38 has no --write-tree; it must not read as "no conflict".
        raise ForkctlError(f"git merge-tree --write-tree is unavailable: {proc.stderr.strip()}")
    return proc.returncode == 0


def mirror_drift(slug: str, upstream: str, stack_patch_ids: set[str]) -> list[str]:
    """Sections 6.2 events 1 and 2: the only two things that need action."""
    ref = mirror_ref(slug)
    if ref is None:
        return ["not fetched locally; run git fetch origin"]

    notes = []
    carried = git_lines("rev-list", f"{upstream}..{ref}")
    if len(carried) != 1:
        notes.append(
            f"carries {len(carried)} commits over upstream, expected exactly 1 -- "
            "the open PR offers more than the stacked commit; rebuild it"
        )

    try:
        if git("rev-parse", f"{ref}^") != git("rev-parse", upstream) and not merges_cleanly(ref, upstream):
            notes.append("event 1: no longer merges onto upstream -- rebuild onto the new tip")
    except ForkctlError as exc:
        notes.append(f"could not test whether it still merges onto upstream: {exc}")

    identity = patch_id(ref)
    if identity is None:
        notes.append("could not compute a patch-id for the mirror tip; drift is unverified")
    elif identity not in stack_patch_ids:
        notes.append(
            "event 2: content differs from every stack commit -- the open PR offers "
            "something we no longer run; rebuild and force-push"
        )
    return notes


def current_branch() -> str:
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    return git("rev-parse", "HEAD") if branch == "HEAD" else branch


def cmd_fork_only(args: argparse.Namespace) -> int:
    for path in FORK_ONLY_PATHS:
        print(path)
    for prefix in FORK_ONLY_PREFIXES:
        print(f"{prefix}*")
    if args.verbose:
        print("\nshared files carrying a fork-only hunk:")
        for path in SHARED_FILES:
            print(f"  {path}")
        print("\nfork-only hunk markers:")
        for marker in FORK_HUNK_MARKERS:
            print(f"  {marker}")
    return 0


def cmd_check_commit(args: argparse.Namespace) -> int:
    """Sections 4.4 and 4.5: branch shape and upstream-cleanliness pre-flight."""
    report = Report("check-commit", args.format)
    if not require_refs(report, args.base):
        return report.finish()

    commits = git_lines("rev-list", f"{args.base}..HEAD")
    one_commit = report.check(
        len(commits) == 1,
        "exactly one commit on the branch",
        f"{len(commits)} commits on the branch, expected exactly 1",
        f"git rebase {args.base} && git reset --soft {args.base} && git commit -F msg",
    )

    # One commit *ahead* is not the same as rebased onto the current base: a
    # branch cut weeks ago is still one commit ahead of where it started.
    if one_commit:
        report.check(
            git("rev-parse", "HEAD^") == git("rev-parse", args.base),
            f"rebased directly onto {args.base}",
            f"the commit is not parented on the current {args.base} tip",
            f"git rebase {args.base}",
        )

    subject = git("log", "-1", "--format=%s") if commits else ""
    problems = subject_problems(subject)
    report.check(
        not problems,
        f"subject is well formed: {subject!r}",
        f"subject {subject!r} " + "; ".join(problems),
        "Conventional Commits, imperative mood, <= 72 chars (section 4.4)",
    )

    is_chore = subject.startswith(FORK_COMMIT_PREFIX)
    if args.chore and not is_chore:
        report.fail(f"--chore given but the subject is not {FORK_COMMIT_PREFIX!r}: {subject!r}")
    if is_chore and not args.chore:
        report.warn(
            "subject is a chore(fork) commit but --chore was not passed; the "
            "fork-only checks below are expected to trip for a fork-only change"
        )

    # Structural validity of the markers is checked in both modes. A chore commit
    # may legitimately rewrite a block, but if it leaves two of them behind, every
    # later feature commit can edit the second one unnoticed.
    malformed = malformed_fork_blocks("HEAD")
    report.check(
        not malformed,
        "fork-only markers are well formed",
        "fork-only marker structure is broken: " + "; ".join(malformed),
        "each shared file carries exactly one fork:begin/fork:end pair",
    )

    # Asserted absolutely, in both modes: a before/after comparison cannot see a
    # fork-only hunk that was already missing before this commit touched it.
    state = fork_state_problems("HEAD")
    report.check(
        not state,
        "fork-only state is intact",
        "fork-only state is broken: " + "; ".join(state),
        "the branch must keep every required fork-only file, the fork block and the branch filter",
    )

    if args.chore:
        report.info("fork-only file and hunk checks: exempt (chore(fork) branch)")
        return report.finish()

    offenders = fork_only_files_in(args.base)
    report.check(
        not offenders,
        "no registered fork-only file in the diff",
        f"fork-only file(s) in the feature diff: {', '.join(offenders)}",
        "move them into a separate chore(fork): commit",
    )

    workflow_problems = fork_workflow_problems(args.base)
    report.check(
        not workflow_problems,
        "fork-only branch filters are untouched in shared workflows",
        "; ".join(workflow_problems),
        "leave the fork branch in the pull_request filter; the rest of the file is yours",
    )

    merge_base = git("merge-base", args.base, "HEAD")
    blocks = sorted(set(changed_fork_blocks(args.base)) | set(changed_fork_markers(merge_base, "HEAD")))
    report.check(
        not blocks,
        "fork-only blocks in shared files are unchanged",
        f"fork-only block content changed in: {', '.join(blocks)}",
        "revert the fork:begin/fork:end block; it belongs in the chore(fork) commit",
    )
    return report.finish()


def verify_stack(report: Report, mirror_ref: str, stack_ref: str) -> str | None:
    """Sections 3.3 and 7: stack invariants. Returns the fork base, or None."""
    if not require_refs(report, mirror_ref, stack_ref):
        return None

    mirror = git("rev-parse", mirror_ref)
    stack = git("rev-parse", stack_ref)

    contains = git_rc("merge-base", "--is-ancestor", mirror, stack) == 0
    if not report.check(
        contains,
        f"{stack_ref} contains the {mirror_ref} tip",
        f"{stack_ref} does not contain the {mirror_ref} tip",
        "rebase the stack (copilot-instructions.md section 7)",
    ):
        return None

    rng = f"{mirror}..{stack}"

    merges = git_lines("rev-list", "--min-parents=2", "--max-count=1", rng)
    report.check(
        not merges,
        "stack is linear (no merge commits)",
        f"merge commit on the stack: {merges[0] if merges else ''}",
        "rebuild the stack with rebase; never merge into integration/3.0",
    )

    subjects = git_lines("log", "--format=%s", rng)

    # Read subjects, not whole messages: `git log --grep` matches the body too,
    # and a leading space is enough to slip past a prefix test.
    ragged = [s for s in subjects if s != s.strip()]
    report.check(
        not ragged,
        "no commit subject has stray leading or trailing whitespace",
        f"commit subject with stray whitespace: {ragged[0]!r}" if ragged else "",
        "reword it; whitespace hides a subject from every prefix check",
    )

    fixups = [s for s in subjects if s.strip().startswith(("fixup!", "squash!"))]
    report.check(
        not fixups,
        "no stray fixup! commit",
        f"stray fixup!/squash! commit on the stack: {fixups[0]!r}" if fixups else "",
        "finish the autosquash rebase before pushing",
    )

    chore_count = sum(1 for s in subjects if s.startswith(FORK_COMMIT_PREFIX))
    report.check(
        chore_count == 1,
        "exactly one chore(fork): commit",
        f"{chore_count} chore(fork): commits on the stack, expected 1",
        "fold fork-only changes into the base commit (section 3.3)",
    )

    commits = git_lines("rev-list", rng)
    if not commits:
        report.fail(f"{stack_ref} has no commits over {mirror_ref}")
        return None

    base = commits[-1]
    base_subject = git("log", "-1", "--format=%s", base)
    report.check(
        base_subject.startswith(FORK_COMMIT_PREFIX),
        f"oldest stack commit is the fork base ({base[:9]})",
        f"oldest stack commit {base[:9]} is not the fork base: {base_subject!r}",
    )

    parents = git("rev-list", "--parents", "-n", "1", base).split()
    if report.check(
        len(parents) == 2,
        "fork base has exactly one parent",
        f"fork base {base[:9]} does not have exactly one parent",
    ):
        report.check(
            parents[1] == mirror,
            "fork base is parented on the mirror tip",
            f"fork base {base[:9]} is parented on {parents[1][:9]}, not the mirror tip {mirror[:9]}",
        )

    # Keeps the hardcoded list honest: anything the fork base introduces is by
    # definition fork-only, so an unregistered file here means the guards that
    # protect feature commits have a blind spot.
    unregistered = [
        path
        for path in diff_files(mirror, base)
        if not is_fork_only_path(path) and path not in SHARED_FILES and path not in FORK_WORKFLOW_FILES
    ]
    report.check(
        not unregistered,
        "every file in the fork base is registered as fork-only",
        f"fork base touches unregistered file(s): {', '.join(unregistered)}",
        "add them to FORK_ONLY_PATHS/FORK_ONLY_PREFIXES in forkctl.py, or move them under .github/fork/",
    )

    malformed = malformed_fork_blocks(base) + malformed_fork_blocks(stack)
    report.check(
        not malformed,
        "fork-only markers are well formed on the stack",
        "fork-only marker structure is broken: " + "; ".join(malformed),
        "each shared file carries exactly one fork:begin/fork:end pair",
    )

    state = fork_state_problems(stack)
    report.check(
        not state,
        "fork-only state is intact at the stack tip",
        "fork-only state is broken: " + "; ".join(state),
        "the stack tip must keep every required fork-only file, the fork block and the branch filter",
    )

    smuggled = smuggled_shared_changes(mirror, base)
    report.check(
        not smuggled,
        "fork base changes shared files only inside the fork-only hunk",
        "fork base smuggles a shared-file change: " + "; ".join(smuggled),
        "move it to its own feature commit where the guards can see it",
    )

    # The checks above describe the tip. Feature commits are what get merged, so
    # each is re-checked against its own parent exactly as check-commit would.
    contaminated = []
    for commit in commits[:-1]:
        parent = f"{commit}^"
        offenders = fork_only_files_in(parent, commit)
        blocks = set(changed_fork_blocks(parent, commit)) | set(changed_fork_markers(parent, commit))
        offenders += [f"{path} (fork block)" for path in sorted(blocks)]
        offenders += fork_workflow_problems(parent, commit)
        if offenders:
            contaminated.append(f"{commit[:9]}: {'; '.join(offenders)}")
    report.check(
        not contaminated,
        f"every feature commit on the stack is upstream-clean ({len(commits) - 1} checked)",
        "contaminated feature commit(s): " + "; ".join(contaminated),
        "fork-only content belongs in the base commit, not in a feature commit",
    )

    return None if report.failed else base


def cmd_check_stack(args: argparse.Namespace) -> int:
    # With --print-base the SHA is the output, so the report belongs on stderr and
    # `BASE=$(forkctl check-stack --print-base) || exit 1` captures only the SHA.
    report = Report("check-stack", args.format, sys.stderr if args.print_base else sys.stdout)
    base = verify_stack(report, args.mirror, args.stack)
    if base is None:
        return report.finish()

    if args.print_base:
        print(base)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if args.github_output and github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"base={base}\n")

    return report.finish()


def resolve_feature_commit(report: Report, args: argparse.Namespace) -> str | None:
    """Section 6: resolve by subject, never by a recorded SHA that rebases stale."""
    if args.sha:
        if not ref_exists(args.sha):
            report.fail(f"commit not found: {args.sha}")
            return None
        sha = git("rev-parse", args.sha)
        # Gate 3 must prove *the* commit being merged. Naming an older stack
        # commit while a different one is checked out certified the wrong diff,
        # so --sha has to be the single commit of the branch under review; use
        # --subject to rebuild the mirror of a commit that is already stacked.
        head = current_branch()
        if head_is_detached():
            report.fail(
                "HEAD is detached",
                "check out the feature branch under review; --subject rebuilds an already-stacked commit",
            )
            return None
        if sha != git("rev-parse", "HEAD"):
            report.fail(
                f"--sha {sha[:9]} is not HEAD ({git('rev-parse', 'HEAD')[:9]})",
                "Gate 3 proves the commit under review; use --subject to rebuild a stacked commit",
            )
            return None
        parented_on_tip = git_rc("rev-parse", "--verify", "--quiet", f"{sha}^") == 0 and git(
            "rev-parse", f"{sha}^"
        ) == git("rev-parse", args.stack)
        if not parented_on_tip:
            report.fail(
                f"{head} is not a single commit on top of the {args.stack} tip",
                "squash to one commit and rebase onto the current stack tip (section 4.4)",
            )
            return None
        return sha

    entries = git_lines("log", "--format=%H%x00%s", f"{args.mirror}..{args.stack}")
    matches = [line.split("\0", 1)[0] for line in entries if line.split("\0", 1)[1] == args.subject]
    if len(matches) != 1:
        report.fail(f"subject matches {len(matches)} stack commits, expected exactly 1: {args.subject!r}")
        return None
    return matches[0]


def restore_checkout(report: Report, started_on: str) -> None:
    """Return to where the caller was, and say so if that proved impossible.

    Printing "back on <branch>" without checking is how an interrupted run left
    people on a conflicted detached HEAD believing it had cleaned up.
    """
    if git_rc("cherry-pick", "--abort") == 0:
        report.info("aborted an in-progress cherry-pick")
    if git_rc("switch", started_on) == 0 or git_rc("switch", "--detach", started_on) == 0:
        report.info(f"back on {started_on}")
        return
    report.fail(
        f"could not return to {started_on}; the checkout is detached",
        f"resolve the working tree, then: git switch {started_on}",
    )


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def cmd_mirror(args: argparse.Namespace) -> int:
    """Gate 3 and section 6: rebuild mirror/<slug> and prove it is upstream-clean."""
    report = Report(f"mirror/{args.slug}", args.format)

    # Validated before anything moves: a slug git rejects as a branch name would
    # otherwise only surface after the checkout, stranding the user detached.
    if not SLUG_RE.match(args.slug):
        report.fail(f"invalid slug {args.slug!r}", "short, lowercase, hyphenated: lane-heading-obs")
        return report.finish()
    if not working_tree_clean():
        report.fail("working tree is dirty", "commit or stash first")
        return report.finish()
    if not require_refs(report, args.upstream, args.mirror, args.stack):
        return report.finish()

    sha = resolve_feature_commit(report, args)
    if sha is None:
        return report.finish()
    report.info(f"feature commit {sha[:9]}  {git('log', '-1', '--format=%s', sha)}")

    branch = f"mirror/{args.slug}"
    started_on = current_branch()

    # Build detached and only move the branch once every check has passed. An
    # existing mirror/<slug> may carry the commit an upstream PR is serving, so
    # it must not be destroyed to find out whether its replacement is valid.
    stayed = False
    try:
        # Inside the try: an interrupt between the switch and the first guarded
        # statement would otherwise skip the restoration entirely.
        git("switch", "--detach", args.upstream)
        if git_rc("cherry-pick", sha) != 0:
            git_rc("cherry-pick", "--abort")
            report.fail(
                "cherry-pick onto pure upstream failed -- the commit is entangled with fork-only state",
                "fix it on the feature branch and re-run",
            )
        else:
            report.ok("cherry-picks onto pure upstream without conflict")

            offenders = fork_only_files_in(args.upstream)
            report.check(
                not offenders,
                "no registered fork-only file in the mirror diff",
                f"fork-only file(s) in the mirror diff: {', '.join(offenders)}",
            )

            markers = fork_markers_anywhere(args.upstream)
            report.check(
                not markers,
                "no fork-only marker added anywhere in the mirror diff",
                f"fork-only marker in the mirror diff: {markers[0] if markers else ''}",
            )

            files = diff_files(args.upstream)
            report.info(f"mirror diff touches {len(files)} file(s):")
            for path in files:
                report.info(f"  {path}")

            candidate = git("rev-parse", "HEAD")
            if report.failed:
                report.info(f"{branch} was left untouched; nothing was published")
            else:
                if ref_exists(branch) and git("rev-parse", branch) != candidate:
                    report.info(f"{branch} moved from {git('rev-parse', branch)[:9]} to {candidate[:9]}")
                git("branch", "-f", branch, candidate)
                if args.stay:
                    git("switch", branch)
                    stayed = True
                    report.info(f"staying on {branch}")
    finally:
        # Unconditional: an exception or a Ctrl-C after the checkout must not
        # leave the caller detached on a half-built or conflicted mirror.
        if not stayed:
            restore_checkout(report, started_on)

    return report.finish()


def upstream_prs_for(slug: str) -> list[dict]:
    """Every upstream PR whose head is our mirror branch, in our fork only.

    GitHub's `--head` filter matches by branch *name* across owners, so another
    fork pushing the same slug would otherwise be mistaken for ours.
    """
    prs = gh_json(
        "pr",
        "list",
        "--repo",
        UPSTREAM_REPO,
        "--head",
        f"mirror/{slug}",
        "--state",
        "all",
        "--limit",
        "100",
        "--json",
        "number,state,headRepositoryOwner",
    )
    return [pr for pr in prs if (pr.get("headRepositoryOwner") or {}).get("login") == FORK_OWNER]


def cmd_delete_mirror(args: argparse.Namespace) -> int:
    """Section 6.2: deleting the head branch closes an open upstream PR."""
    report = Report(f"delete mirror/{args.slug}", args.format)
    if not gh_available():
        report.fail("gh is not installed; cannot verify the upstream PR state")
        return report.finish()

    try:
        prs = upstream_prs_for(args.slug)
    except ForkctlError as exc:
        report.fail(f"could not verify the upstream PR state: {exc}", "retry; never delete unverified")
        return report.finish()

    live = [pr for pr in prs if pr["state"] not in TERMINAL_PR_STATES]
    if live:
        report.fail(
            f"upstream PR(s) {', '.join('#' + str(pr['number']) for pr in live)} for mirror/{args.slug} "
            f"are {', '.join(pr['state'] for pr in live)} -- deleting the branch would "
            "close them and read as a silent withdrawal"
        )
        return report.finish()
    states = ", ".join(pr["state"] for pr in prs) or "NONE"
    report.ok(f"every upstream PR for this slug is terminal ({states}); deletion is safe")

    if args.dry_run:
        report.info("dry run; nothing deleted")
        return report.finish()

    if git_rc("push", "origin", "--delete", f"mirror/{args.slug}") != 0:
        report.fail("deleting the branch on origin failed", "check the remote and retry")
        return report.finish()
    if git_lines("ls-remote", "--heads", "origin", f"mirror/{args.slug}"):
        report.fail("the branch still exists on origin after the delete")
        return report.finish()
    report.ok("deleted on origin")

    # The remote delete is the part that matters for the upstream PR; a failed
    # local delete (usually because it is the current branch) is still a failure
    # to report rather than fold into a single cheerful line.
    if not ref_exists(f"mirror/{args.slug}"):
        report.ok("no local branch left")
        return report.finish()
    report.check(
        git_rc("branch", "-D", f"mirror/{args.slug}") == 0 and not ref_exists(f"mirror/{args.slug}"),
        "deleted locally",
        f"the local branch mirror/{args.slug} could not be deleted",
        "switch away from it and run `git branch -D` by hand",
    )
    return report.finish()


def cmd_check_view(args: argparse.Namespace) -> int:
    """Section 2: fork-only/3.0 is a derived view, republished by hand."""
    report = Report("check-view", args.format)
    base = verify_stack(report, args.mirror, args.stack)
    if base is None:
        return report.finish()
    report.ok(f"stack is sound; fork base is {base[:9]}")

    fix = f"git push --force origin {base}:refs/heads/{VIEW_BRANCH}"
    published = ""
    for line in git_lines("ls-remote", "origin", f"refs/heads/{VIEW_BRANCH}"):
        published = line.split("\t")[0] if "\t" in line else line.split()[0]

    if not published:
        report.fail(f"{VIEW_BRANCH} does not exist", fix)
    else:
        report.check(
            published == base,
            f"{VIEW_BRANCH} is current at {base[:9]}",
            f"{VIEW_BRANCH} is stale ({published[:9]}); the fork base is {base[:9]}",
            fix,
        )

    if not gh_available():
        report.fail(
            "gh is not installed; the standing PR contract could not be verified",
            "install gh, or run this check where gh is available",
        )
        return report.finish()

    try:
        prs = gh_json(
            "pr",
            "list",
            "--repo",
            FORK_REPO,
            "--head",
            VIEW_BRANCH,
            "--state",
            "all",
            "--limit",
            "100",
            "--json",
            "number,state,baseRefName,isDraft,labels",
        )
    except ForkctlError as exc:
        report.fail(f"could not query the standing view PR: {exc}", "retry; an unverified contract is not a pass")
        return report.finish()

    merged = [pr for pr in prs if pr["state"] == "MERGED"]
    if not report.check(
        not merged,
        f"no {VIEW_BRANCH} PR was ever merged",
        f"a {VIEW_BRANCH} PR was MERGED into the mirror -- the mirror is contaminated: "
        + ", ".join(f"#{pr['number']}" for pr in merged),
        "reset 3.0 to upstream/3.0 (admin action, section 7)",
    ):
        return report.finish()

    open_prs = [pr for pr in prs if pr["state"] == "OPEN"]
    if not open_prs:
        report.fail(
            f"no standing {VIEW_BRANCH} PR is open -- section 2 requires one, opened by hand",
            f"open a draft PR {VIEW_BRANCH} -> 3.0 labelled do-not-merge",
        )
        return report.finish()
    if not report.check(
        len(open_prs) == 1,
        "exactly one standing view PR is open",
        f"{len(open_prs)} open {VIEW_BRANCH} PRs; exactly one is expected",
    ):
        return report.finish()

    pr = open_prs[0]
    labels = {label["name"] for label in pr["labels"]}
    broken = []
    if pr["baseRefName"] != "3.0":
        broken.append(f"base={pr['baseRefName']}, expected 3.0")
    if not pr["isDraft"]:
        broken.append("not a draft")
    if "do-not-merge" not in labels:
        broken.append("missing the do-not-merge label")
    # Both labels together are the in-flight-process-change signal that `doctor`
    # queries; a permanent PR carrying both would raise it forever.
    if "do-not-mirror" in labels:
        broken.append("carries do-not-mirror, which a standing PR must never have")

    report.check(
        not broken,
        f"standing view PR #{pr['number']} keeps its contract",
        f"standing view PR #{pr['number']} breaks its contract: " + "; ".join(broken),
    )
    return report.finish()


def cmd_status(args: argparse.Namespace) -> int:
    """Sections 6.2, 6.3 and 7: divergence report and what is in flight."""
    report = Report("status", "text")
    if not require_refs(report, args.mirror, args.stack):
        return report.finish()

    rng = f"{args.mirror}..{args.stack}"
    subjects = git_lines("log", "--format=%s", rng)
    fork_only = sum(1 for s in subjects if s.startswith(FORK_COMMIT_PREFIX))

    print(f"  total gap   : {len(subjects)}")
    print(f"  fork-only   : {fork_only}  (permanent floor, never drive to zero)")
    print(f"  feature gap : {len(subjects) - fork_only}")
    print("\n  stack (newest first):")
    for line in git_lines("log", "--format=%h %s", rng):
        print(f"    {line}")

    if ref_exists(args.upstream):
        stale = [line[2:] for line in git_lines("cherry", args.upstream, args.stack) if line.startswith("- ")]
        if stale:
            report.warn(f"{len(stale)} stack commit(s) already upstream (section 6.3)")
            for sha in stale:
                print(f"    - {git('log', '-1', '--format=%h %s', sha)}")
        else:
            print("\n  no stale commits: nothing we carry is already upstream")
    else:
        report.fail(f"{args.upstream} not fetched; the stale-commit audit could not run", "git fetch upstream")
        return report.finish()

    remote = git_lines("ls-remote", "--heads", "origin", "mirror/*")
    slugs = [line.split("refs/heads/mirror/", 1)[1] for line in remote if "refs/heads/mirror/" in line]

    if not gh_available():
        print("\n  mirror branches on origin:")
        for name in slugs or ["(none)"]:
            print(f"    mirror/{name}")
        report.fail("gh is not installed; the upstream PR inventory could not run")
        return report.finish()

    try:
        prs = gh_json(
            "pr",
            "list",
            "--repo",
            UPSTREAM_REPO,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,headRefName,headRepositoryOwner,title",
        )
    except ForkctlError as exc:
        report.fail(f"could not query upstream PRs: {exc}", "retry; an unverified inventory is not a pass")
        return report.finish()

    open_prs = {pr["headRefName"]: pr for pr in prs if (pr.get("headRepositoryOwner") or {}).get("login") == FORK_OWNER}

    stack_patch_ids = {
        identity for identity in (patch_id(sha) for sha in git_lines("rev-list", rng)) if identity is not None
    }

    print("\n  mirror branches on origin:")
    if not slugs:
        print("    (none)")
    for name in slugs:
        pr = open_prs.get(f"mirror/{name}")
        if pr is None:
            print(f"    mirror/{name}  --  no open upstream PR (finished, or never opened)")
            continue
        print(f"    mirror/{name}  --  upstream #{pr['number']} open")
        for note in mirror_drift(name, args.upstream, stack_patch_ids):
            # Drift is a warning; being unable to measure it is a failure.
            if note.startswith(("could not", "not fetched")):
                report.fail(f"mirror/{name}: {note}")
            else:
                report.warn(f"mirror/{name}: {note}")

    orphans = set(open_prs) - {f"mirror/{name}" for name in slugs}
    for head in sorted(orphans):
        report.warn(f"{head} has an open upstream PR but no branch on origin")

    return report.finish()


def cmd_doctor(args: argparse.Namespace) -> int:
    """Section 4.1: is this clone set up, and is the process itself mid-change?"""
    report = Report("doctor", args.format)

    report.check(
        git_rc("remote", "get-url", "upstream") == 0,
        "upstream remote is configured",
        "upstream remote is missing",
        f"git remote add upstream {UPSTREAM_URL}",
    )

    venv = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    report.check(
        os.path.exists(venv),
        f"virtualenv present ({venv})",
        f"no virtualenv at {venv}",
        "uv venv && uv pip install -e .",
    )

    if not gh_available():
        report.fail(
            "gh is not installed; cannot tell whether the process is mid-change",
            "install gh; an unchecked process state is not a pass",
        )
        return report.finish()

    try:
        inflight = gh_json(
            "pr",
            "list",
            "--repo",
            FORK_REPO,
            "--state",
            "open",
            "--label",
            "do-not-mirror",
            "--label",
            "do-not-merge",
            "--json",
            "number,title",
        )
    except ForkctlError as exc:
        report.fail(f"could not query in-flight process PRs: {exc}", "retry before relying on the written process")
        return report.finish()

    if not inflight:
        report.ok("no in-flight process change; copilot-instructions.md is current")
        return report.finish()

    report.warn(
        "the fork process is mid-change; these PRs supersede "
        "copilot-instructions.md wherever they overlap -- read them first:"
    )
    for pr in inflight:
        report.info(f"  #{pr['number']} {pr['title']}")
    return report.finish()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forkctl",
        description="Fork-process checks for ASCS-eV/PufferDrive.",
        epilog="The process itself is described in .github/copilot-instructions.md.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_fmt(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--format",
            choices=("text", "github"),
            default="text",
            help="output style; 'github' emits ::error:: annotations",
        )

    p = sub.add_parser("doctor", help="clone setup and in-flight process changes")
    add_fmt(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "check-commit",
        help="one commit, no fork-only content (sections 4.4, 4.5)",
    )
    p.add_argument("--base", default=STACK_REF)
    p.add_argument(
        "--chore",
        action="store_true",
        help="fork-only branch: skip the fork-only checks, keep the one-commit check",
    )
    add_fmt(p)
    p.set_defaults(func=cmd_check_commit)

    p = sub.add_parser("check-stack", help="stack invariants (sections 3.3, 7)")
    p.add_argument("--mirror", default=MIRROR_REF)
    p.add_argument("--stack", default=STACK_REF)
    p.add_argument("--print-base", action="store_true", help="print the fork base SHA")
    p.add_argument(
        "--github-output",
        action="store_true",
        help="also append base=<sha> to $GITHUB_OUTPUT",
    )
    add_fmt(p)
    p.set_defaults(func=cmd_check_stack)

    p = sub.add_parser("check-view", help="fork-only/3.0 is current and its standing PR is intact")
    p.add_argument("--mirror", default=MIRROR_REF)
    p.add_argument("--stack", default=STACK_REF)
    add_fmt(p)
    p.set_defaults(func=cmd_check_view)

    p = sub.add_parser("mirror", help="rebuild mirror/<slug> and prove it is upstream-clean (gate 3)")
    p.add_argument("slug")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--sha", help="feature commit to cherry-pick")
    group.add_argument("--subject", help="resolve the commit by its exact subject")
    p.add_argument("--upstream", default=UPSTREAM_REF)
    p.add_argument("--mirror", default=MIRROR_REF)
    p.add_argument("--stack", default=STACK_REF)
    p.add_argument("--stay", action="store_true", help="stay on the mirror branch")
    add_fmt(p)
    p.set_defaults(func=cmd_mirror)

    p = sub.add_parser("delete-mirror", help="delete mirror/<slug> only if its upstream PR is terminal")
    p.add_argument("slug")
    p.add_argument("--dry-run", action="store_true")
    add_fmt(p)
    p.set_defaults(func=cmd_delete_mirror)

    p = sub.add_parser("status", help="divergence report and in-flight mirrors")
    p.add_argument("--mirror", default=MIRROR_REF)
    p.add_argument("--stack", default=STACK_REF)
    p.add_argument("--upstream", default=UPSTREAM_REF)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("fork-only", help="print the canonical fork-only path list")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_fork_only)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ForkctlError as exc:
        print(f"forkctl: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
