"""Read-only public evidence hygiene and scope check; no personal store access."""
from hashlib import sha256
import re
import subprocess

from aliases import read
from runner import HERE, load


def main():
    paths = []
    patterns = [re.compile(r"C:[\\/]+Users[\\/]+(?!<user>)[^\s\\/\"']+", re.I), re.compile(r"\bDESKTOP-[A-Z0-9]{4,}\b", re.I)]
    for path in HERE.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".md", ".py", ".txt", ".svg"}:
            if any(p.search(path.read_text(encoding="utf-8")) for p in patterns):
                paths.append(str(path.relative_to(HERE)))
    assert not paths, "Identity-bearing public files: " + ", ".join(paths)
    blind = {c["query"] for c in load("blind")}
    overlaps = {split: len(blind & {c["query"] for c in load(split)}) for split in ("dev", "challenge", "holdout", "review")}
    assert not any(overlaps.values()), "Exact query reuse"
    digest = sha256((HERE / "blind.json").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert digest == read(HERE / "blind-manifest.json")["sha256_lf"]
    root = HERE.parents[2]
    product = subprocess.check_output(["git", "diff", "origin/main", "--", "py/src", "packages"], cwd=root)
    assert not product, "Product source difference"
    names = subprocess.check_output(["git", "diff", "--name-only", "origin/main"], cwd=root, text=True).splitlines()
    assert all(n.startswith("bench/") for n in names), "Out-of-scope tracked edit"
    print({"public_identity_matches": 0, "blind_query_overlaps": overlaps, "blind_hash_unchanged": True, "product_diff_empty": True, "changed_files_all_bench": True})


if __name__ == "__main__":
    main()
