from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_control_center.agents.credential_preflight import (  # noqa: E402
    refs_from_environment,
    run_credential_preflight,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate AIDO credential refs without printing secret values.",
    )
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        help="Credential ref to validate. Repeat for multiple refs.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch remote/keyring/env values to verify they resolve. Values are never printed.",
    )
    argv = sys.argv[1:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    refs = args.ref or refs_from_environment()
    report = run_credential_preflight(refs, fetch=args.fetch)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
