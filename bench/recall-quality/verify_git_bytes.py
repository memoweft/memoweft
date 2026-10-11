"""Verify frozen fixtures from exact Git object bytes, as in review hash_probe.py."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import evaluate as e


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--split", choices=["dev", "challenge", "holdout", "review", "all"], default="all")
    args = parser.parse_args()
    splits = ["dev", "challenge", "holdout", "review"] if args.split == "all" else [args.split]
    original_here, original_argv = e.HERE, sys.argv
    try:
        with TemporaryDirectory(prefix="memoweft-recall-git-bytes-") as folder:
            e.HERE = Path(folder)
            names = [f"{split}.json" for split in splits] + ["regression-baseline.json"]
            for name in names:
                raw = subprocess.check_output(["git", "show", f"{args.ref}:bench/recall-quality/{name}"], cwd=e.ROOT)
                (e.HERE / name).write_bytes(raw)
            baseline = json.loads((e.HERE / "regression-baseline.json").read_text(encoding="utf-8"))
            for split in splits:
                raw = (e.HERE / f"{split}.json").read_bytes()
                assert e.fixture_hash(raw) == baseline["fixture_hashes"][split]
                assert e.fixture_hash(raw.replace(b"\n", b"\r\n")) == e.fixture_hash(raw), "CRLF checkout differs"
            sys.argv = ["evaluate.py", "--split", args.split, "--check", "--exact-baseline", "--repeats", "1"]
            e.main()
            print("Git object bytes and simulated CRLF checkout passed for " + ", ".join(splits))
    finally:
        e.HERE, sys.argv = original_here, original_argv


if __name__ == "__main__":
    main()
