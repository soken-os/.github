"""Unserved-program sentinel: the production caller for the orphan detector.

Finding M3. Program-scoped dispatch closed double-dispatch but opened its
opposite: a work item seeded with a typo'd or not-yet-configured program is
claimed by no controller and sits forever, invisible. `unserved_programs()`
computes that condition — but a detector nothing invokes does not close silent
invisibility, so this is the deterministic observer that actually runs it.

Run it on a schedule (or at activation) with the explicit set of programs that
really have controllers. It exits non-zero and notifies when any nonterminal
work belongs to a program nobody serves.

    python -m reference.cec.phase3.sentinel \
        --program CEC --program carlson-roofing \
        --notify-command /path/to/notify.sh

The configured set must come from configuration — never derived from the rows
being checked, which is what made the original test tautological.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .controller import unserved_programs

EXIT_UNSERVED = 22


def load_configured(args: argparse.Namespace) -> list[str]:
    """The explicit configured-program set, from flags and/or a config file."""

    configured = list(args.program or [])
    if args.programs_file:
        text = Path(args.programs_file).read_text(encoding="utf-8")
        configured += [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    return sorted(set(configured))


def notify(notify_command: str | None, key: str, message: str) -> bool:
    """Best-effort alarm; returns whether it was delivered."""

    if not notify_command:
        return False
    try:
        subprocess.run(
            (*shlex.split(notify_command), key, message), check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def run(args: argparse.Namespace) -> int:
    configured = load_configured(args)
    if not configured:
        # Fail closed: with no configured set every program would look unserved,
        # or (worse) an empty check would look like a clean bill of health.
        print(
            "refusing to run: no configured programs supplied "
            "(--program and/or --programs-file)",
            file=sys.stderr,
        )
        return 2

    unserved = unserved_programs(configured)
    print(
        json.dumps(
            {
                "configured": configured,
                "unserved": [
                    {"program": program, "nonterminal_items": count}
                    for program, count in unserved
                ],
            }
        )
    )
    if not unserved:
        return 0

    detail = ", ".join(f"{program} ({count} items)" for program, count in unserved)
    message = (
        f"CEC sentinel: work is queued for program(s) NO controller serves — {detail}. "
        "These items will never be dispatched until a controller is configured "
        "or the program name is corrected."
    )
    notify(args.notify_command, "cec-unserved-programs", message)
    print(message, file=sys.stderr)
    return EXIT_UNSERVED


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--program",
        action="append",
        help="a program that HAS a controller; repeat for each (configuration, "
        "never derived from the registry)",
    )
    result.add_argument(
        "--programs-file",
        help="file with one configured program per line ('#' comments allowed)",
    )
    result.add_argument(
        "--notify-command",
        help="command prefix; key and message are appended",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
