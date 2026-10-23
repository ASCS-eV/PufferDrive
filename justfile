# PufferDrive task runner. Run `just` with no arguments to list the recipes.
#
# Recipe bodies are single commands that delegate to scripts/devtasks.py, so
# they behave identically under sh, PowerShell and cmd.

set windows-shell := ["cmd.exe", "/c"]

# Project interpreter. Override it for a non-standard env: `just python=/path/to/python test`
python := if os_family() == "windows" { '.venv\Scripts\python.exe' } else { '.venv/bin/python' }

# Exported so helpers read the path from the environment, immune to shell quoting.
export PUFFER_PYTHON := python

# The C tests and Docker suite need no project venv, only a toolchain.
system_python := if os_family() == "windows" { 'python' } else { 'python3' }

# List the available recipes
default:
    @just --list --unsorted

alias help := default

# Run the local suites: unit + eval + C + notebooks (not docker-smoke or perf)
test: test-unit test-eval test-c test-notebooks

# Verify the project interpreter exists before running the Python suites
check-venv:
    @{{ system_python }} scripts/devtasks.py check-venv

# Rebuild the C extension only when a .c/.h file changed
build: check-venv
    {{ python }} scripts/devtasks.py build-ext

# Force-rebuild the C extension unconditionally
rebuild: check-venv
    {{ python }} scripts/devtasks.py build-ext --force

# Python unit tests (tests/unit_tests)
test-unit: build
    {{ python }} -m pytest -v tests/unit_tests

# Evaluation integration tests (tests/eval)
test-eval: build
    {{ python }} -m pytest -v tests/eval

# C sim tests: dynamics, geometry, IDM, collisions (needs a C compiler, not the venv)
test-c:
    {{ system_python }} scripts/devtasks.py c-tests

# Execute every checked-in notebook end-to-end
test-notebooks: build ensure-test-deps
    {{ python }} -m pytest -v tests/notebooks

# Full smoke suite in Docker (train/rollout goldens)
test-docker-smoke:
    {{ system_python }} scripts/devtasks.py docker-smoke

# C perf benchmark for hot-path work
perf:
    {{ system_python }} scripts/devtasks.py c-perf

# Install notebook test deps when they are missing
ensure-test-deps:
    @{{ python }} scripts/devtasks.py ensure-test-deps

# Remove compiled C test binaries
clean:
    {{ system_python }} scripts/devtasks.py clean
