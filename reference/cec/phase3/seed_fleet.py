"""Seed the 10-reviewer fleet into the durable registry."""

from __future__ import annotations

from .fleet import seed_fleet


def main() -> int:
    ids = seed_fleet()
    for work_item_id in ids:
        print(work_item_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
