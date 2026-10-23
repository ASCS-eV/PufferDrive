#!/usr/bin/env python3
"""Cross-platform plumbing behind the justfile recipes.

just has no prerequisite graph, so the staleness checks the Makefile used to get
from Make live here instead. Staleness is mtime-based, matching the Makefile it
replaces.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVE_SOURCE_DIR = REPO_ROOT / "pufferlib" / "ocean" / "drive"
C_TEST_DIR = REPO_ROOT / "tests" / "drive"
C_TEST_BUILD_DIR = C_TEST_DIR / "build"
SMOKE_DOCKERFILE = REPO_ROOT / "tests" / "smoke_tests" / "Dockerfile"
SMOKE_IMAGE_TAG = "pufferdrive-smoke"

UNIT_TEST_SOURCES = (
    "test_error.c",
    "test_drive_geometry.c",
    "test_drive_observations_rewards.c",
    "test_drive_goals.c",
    "test_drive_metrics.c",
    "test_drive_red_light.c",
    "test_drive_stop_sign.c",
    "test_drive_traffic_lights.c",
    "test_drive_dynamics.c",
    "test_drive_map_cache.c",
    "test_drive_idm.c",
    "test_drive_speed_limits.c",
    "test_drive_cosim_setters.c",
)
SMOKE_TEST_SOURCES = ("test_drive_env_smoke.c",)
PERF_TEST_SOURCES = ("test_perf.c",)

# These two fork() a child to assert on abort paths; Windows has no equivalent.
POSIX_ONLY_TEST_SOURCES = frozenset({"test_error.c", "test_drive_map_cache.c"})

C_TEST_CFLAGS = (
    "-O0",
    "-g",
    "-std=c99",
    "-D_GNU_SOURCE",
    f"-I{REPO_ROOT.as_posix()}",
    "-Wall",
    "-Wextra",
    "-Wno-unused-function",
    "-Wno-unused-variable",
    "-Wno-unused-parameter",
    "-Wno-implicit-function-declaration",
)
C_TEST_LDLIBS = ("-lm",)
C_TEST_FIXTURE_HEADERS = ("include/test.h", "include/drive_fixture.h")

TEST_DEP_MODULES = ("jupytext", "nbclient", "ipykernel")


def binding_path() -> Path:
    extension_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not extension_suffix:
        fail("could not resolve EXT_SUFFIX from the running interpreter")
    return DRIVE_SOURCE_DIR / f"binding{extension_suffix}"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def newest_mtime(paths) -> float:
    mtimes = [path.stat().st_mtime for path in paths if path.exists()]
    if not mtimes:
        fail("no source files found; is the checkout complete?")
    return max(mtimes)


def is_stale(target: Path, sources) -> bool:
    if not target.exists():
        return True
    return newest_mtime(sources) > target.stat().st_mtime


def run(command, cwd: Path = REPO_ROOT) -> int:
    printable = " ".join(str(part) for part in command)
    print(f"$ {printable}", flush=True)
    try:
        return subprocess.call([str(part) for part in command], cwd=str(cwd))
    except FileNotFoundError:
        fail(f"{command[0]} not found on PATH")
        return 1


def check(command, cwd: Path = REPO_ROOT) -> None:
    exit_code = run(command, cwd=cwd)
    if exit_code != 0:
        raise SystemExit(exit_code)


def drive_extension_sources():
    return sorted(DRIVE_SOURCE_DIR.glob("*.c")) + sorted(DRIVE_SOURCE_DIR.glob("*.h")) + [REPO_ROOT / "setup.py"]


def build_ext(force: bool) -> None:
    if not force and not is_stale(binding_path(), drive_extension_sources()):
        print("C extension is up to date; skipping rebuild.")
        return
    check([sys.executable, "setup.py", "build_ext", "--inplace", "--force"])


def c_test_headers():
    fixture_headers = [C_TEST_DIR / name for name in C_TEST_FIXTURE_HEADERS]
    return fixture_headers + sorted(DRIVE_SOURCE_DIR.glob("*.h"))


def selected_c_test_sources(source_names):
    if os.name != "nt":
        return list(source_names), []
    runnable = [name for name in source_names if name not in POSIX_ONLY_TEST_SOURCES]
    skipped = [name for name in source_names if name in POSIX_ONLY_TEST_SOURCES]
    return runnable, skipped


def announce_skipped(skipped) -> None:
    if not skipped:
        return
    print("", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print("SKIPPING POSIX-ONLY C TESTS ON WINDOWS -- THIS RUN IS NOT A FULL PASS", file=sys.stderr)
    for name in skipped:
        print(f"  - {name}  (needs fork()/sys/wait.h)", file=sys.stderr)
    print("Run these under WSL, Linux, or macOS before claiming the C suite is green.", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print("", file=sys.stderr)


def compiler_command():
    return os.environ.get("CC", "gcc")


def binary_path(source_name: str) -> Path:
    stem = Path(source_name).stem
    suffix = ".exe" if os.name == "nt" else ""
    return C_TEST_BUILD_DIR / f"{stem}{suffix}"


def compile_c_test(source_name: str) -> None:
    source = C_TEST_DIR / source_name
    if not source.exists():
        fail(f"missing C test source: {source}")
    binary = binary_path(source_name)
    if not is_stale(binary, [source, *c_test_headers()]):
        return
    C_TEST_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    check(
        [compiler_command(), *C_TEST_CFLAGS, str(source), *C_TEST_LDLIBS, "-o", str(binary)],
        cwd=C_TEST_DIR,
    )


def run_c_test_group(source_names) -> None:
    runnable, skipped = selected_c_test_sources(source_names)
    announce_skipped(skipped)
    for source_name in runnable:
        compile_c_test(source_name)
    for source_name in runnable:
        check([binary_path(source_name)])


def check_venv() -> None:
    interpreter = os.environ.get("PUFFER_PYTHON")
    if not interpreter:
        fail("PUFFER_PYTHON is unset; run this through the justfile")
    if Path(interpreter).exists() or shutil.which(interpreter):
        return
    print(f"error: project interpreter not found: {interpreter}", file=sys.stderr)
    print("Create it with:  uv venv && uv pip install -e .", file=sys.stderr)
    if os.name == "nt":
        print("", file=sys.stderr)
        print(
            "On Windows the C extension does not build natively: setup.py only emits "
            "GCC/Clang flags and drive.h needs unistd.h. Run the Python suites under "
            "WSL, Linux, macOS, or `just test-docker-smoke`. `just test-c` does work "
            "natively and needs no venv.",
            file=sys.stderr,
        )
    raise SystemExit(1)


def ensure_test_deps() -> None:
    probe = [sys.executable, "-c", "import " + ", ".join(TEST_DEP_MODULES)]
    if subprocess.call(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        return
    if not shutil.which("uv"):
        fail("uv not found; install it or run: pip install -e '.[test]'")
    check(["uv", "pip", "install", "--python", sys.executable, "-e", ".[test]"])


def docker_smoke() -> None:
    if not shutil.which("docker"):
        fail(
            "docker not found. This suite runs in a Linux container -- "
            "install Docker Desktop, or rely on the CI smoke job instead."
        )
    check(["docker", "build", "-f", str(SMOKE_DOCKERFILE), "-t", SMOKE_IMAGE_TAG, "."])
    check(["docker", "run", "--rm", SMOKE_IMAGE_TAG])


def clean_c_tests() -> None:
    shutil.rmtree(C_TEST_BUILD_DIR, ignore_errors=True)
    print(f"removed {C_TEST_BUILD_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_ext_parser = subparsers.add_parser("build-ext", help="build the C extension if its sources changed")
    build_ext_parser.add_argument("--force", action="store_true", help="rebuild unconditionally")

    subparsers.add_parser("check-venv", help="verify the project interpreter exists")

    subparsers.add_parser("c-tests", help="build and run the C unit and smoke tests")
    subparsers.add_parser("c-perf", help="build and run the C perf benchmark")
    subparsers.add_parser("ensure-test-deps", help="install notebook test deps when missing")
    subparsers.add_parser("docker-smoke", help="run the Docker smoke suite")
    subparsers.add_parser("clean", help="remove compiled C test binaries")

    args = parser.parse_args()
    if args.command == "build-ext":
        build_ext(args.force)
    elif args.command == "check-venv":
        check_venv()
    elif args.command == "c-tests":
        run_c_test_group(UNIT_TEST_SOURCES + SMOKE_TEST_SOURCES)
    elif args.command == "c-perf":
        run_c_test_group(PERF_TEST_SOURCES)
    elif args.command == "ensure-test-deps":
        ensure_test_deps()
    elif args.command == "docker-smoke":
        docker_smoke()
    elif args.command == "clean":
        clean_c_tests()


if __name__ == "__main__":
    main()
