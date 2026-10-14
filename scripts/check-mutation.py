#!/usr/bin/env python3
"""Run mutmut and fail below the kill-rate floor.

mutmut exits 0 even when mutants survive, so this gate parses the run
statistics and enforces a minimum kill rate: killed plus timed-out mutants
over everything that could have been caught. Survivors and uncovered mutants
count against the rate; skipped and suspicious mutants do not. The floor rises
as survivors are triaged, never falls.

    uv run python scripts/check-mutation.py --min-kill-rate 70
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# The final summary line mutmut prints, in the emoji order of mutmut.stats:
# done/total, killed, no tests, timeout, suspicious, survived, skipped.
STATS = re.compile(
    r"(?P<done>\d+)/(?P<total>\d+)\s+🎉\s*(?P<killed>\d+)"
    r"\s*🫥\s*(?P<uncovered>\d+)\s*⏰\s*(?P<timeout>\d+)\s*🤔\s*(?P<suspicious>\d+)"
    r"\s*🙁\s*(?P<survived>\d+)\s*🔇\s*(?P<skipped>\d+)"
)


def last_stats(output: str) -> dict[str, int] | None:
    stats = None
    for match in STATS.finditer(output):
        stats = {key: int(value) for key, value in match.groupdict().items()}
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-kill-rate", type=float, required=True)
    arguments = parser.parse_args()

    completed = subprocess.run(
        ["mutmut", "run"],
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        sys.stderr.write(output)
        sys.stderr.write(f"mutation gate failed: mutmut exited {completed.returncode}\n")
        return 2
    stats = last_stats(output)
    if stats is None:
        sys.stderr.write(output)
        sys.stderr.write("mutation gate failed: no mutmut statistics found\n")
        return 2
    if stats["done"] != stats["total"]:
        sys.stderr.write(f"mutation gate failed: only {stats['done']} of {stats['total']} mutants ran\n")
        return 2

    caught = stats["killed"] + stats["timeout"]
    catchable = caught + stats["survived"] + stats["uncovered"]
    rate = caught / catchable if catchable else 1.0
    if rate < arguments.min_kill_rate / 100:
        sys.stderr.write(
            f"mutation gate failed: kill rate {rate:.1%} is below the floor of {arguments.min_kill_rate:.0f}%\n"
        )
        return 1
    print(f"mutation gate passed: kill rate {rate:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
