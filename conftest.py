"""Shared pytest config: skip capability tests inside the worker sandbox (G8).

Some tests exercise host capabilities that a confined CEC worker cannot use from
*inside* its own macOS sandbox:
  * `ps` (process-identity checks) — denied to the sandboxed worker;
  * launching `sandbox-exec` — macOS forbids nesting a sandbox inside a sandbox.

A worker runs the suite as its acceptance gate; those tests would *fail* purely
because of the confinement, not because of any real defect, so the worker would
never claim a completed result. The adapter sets `CEC_WORKER_SANDBOX=1` when it
launches a confined worker; here we turn that into an explicit SKIP for tests
marked `requires_unsandboxed`. Outside the worker (dev, controller-side, CI) the
var is unset and these tests run normally — so coverage is never silently lost,
only relocated to where the capability exists.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_unsandboxed: test needs host capabilities (ps, sandbox-exec) "
        "unavailable inside a confined worker; skipped when CEC_WORKER_SANDBOX=1.",
    )


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("CEC_WORKER_SANDBOX"):
        return
    skip = pytest.mark.skip(
        reason="requires host capability unavailable inside the worker sandbox "
        "(CEC_WORKER_SANDBOX=1); runs unsandboxed elsewhere"
    )
    for item in items:
        if "requires_unsandboxed" in item.keywords:
            item.add_marker(skip)
