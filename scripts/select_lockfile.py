#!/usr/bin/env python3
"""Select the frozen dependency lock for the active Python minor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


SUPPORTED_MINORS = frozenset((3, minor) for minor in range(10, 15))


def _python_minor(value: str) -> tuple[int, int]:
    try:
        major_text, minor_text, *_ = value.split(".")
        version = (int(major_text), int(minor_text))
    except (AttributeError, TypeError, ValueError):
        raise argparse.ArgumentTypeError("python version must look like 3.11") from None
    if version not in SUPPORTED_MINORS:
        raise argparse.ArgumentTypeError("supported Python minors are 3.10 through 3.14")
    return version


def lockfile_for(root: Path, version: tuple[int, int]) -> Path:
    major, minor = version
    return root.resolve() / "requirements-lock" / f"py{major}{minor}.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing requirements-lock/",
    )
    parser.add_argument(
        "--python-version",
        type=_python_minor,
        default=(sys.version_info.major, sys.version_info.minor),
        help="Python minor to select (defaults to the running interpreter)",
    )
    parser.add_argument("--check", action="store_true", help="Fail if the selected lock is absent")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    version = args.python_version
    if version not in SUPPORTED_MINORS:
        print("supported Python minors are 3.10 through 3.14", file=sys.stderr)
        return 2
    lockfile = lockfile_for(args.root, version)
    if args.check and not lockfile.is_file():
        print(f"missing frozen lockfile: {lockfile}", file=sys.stderr)
        return 1
    print(lockfile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
