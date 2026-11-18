"""Download Chinese Wikipedia shards from HuggingFace mirror.

Usage:
    python scripts/download_corpus.py            # dev sample: shard 02 only (~127 MB)
    python scripts/download_corpus.py --all      # full dump: all 6 shards (~1.72 GB)

Existing files are skipped, so re-running is safe.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlopen

BASE = "https://hf-mirror.com/datasets/wikimedia/wikipedia/resolve/main/20231101.zh"
SHARDS = {
    0: ("train-00000-of-00006.parquet", 587.1),
    1: ("train-00001-of-00006.parquet", 263.6),
    2: ("train-00002-of-00006.parquet", 126.8),
    3: ("train-00003-of-00006.parquet", 262.0),
    4: ("train-00004-of-00006.parquet", 256.7),
    5: ("train-00005-of-00006.parquet", 224.9),
}
OUT_DIR = Path("data/raw")
MIN_BYTES = 1_000_000  # anything smaller is a broken download


def download(shard: int, name: str) -> None:
    dest = OUT_DIR / f"zhwiki-{shard:05d}.parquet"
    if dest.exists() and dest.stat().st_size >= MIN_BYTES:
        print(f"skip (exists): {dest} [{dest.stat().st_size / 1e6:.1f} MB]")
        return
    if dest.exists():
        dest.unlink()  # broken/partial file from an interrupted run

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"download: {name}")
    with urlopen(f"{BASE}/{name}", timeout=60) as resp, dest.open("wb") as f:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / total * 100:5.1f}%  {done / 1e6:6.1f}/{total / 1e6:.1f} MB",
                      end="", flush=True)
    print(f"\nok: {dest} [{dest.stat().st_size / 1e6:.1f} MB]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="download all 6 shards (~1.72 GB)")
    args = ap.parse_args()

    shards = SHARDS if args.all else {2: SHARDS[2]}
    total = sum(size for _, size in shards.values())
    print(f"target: {len(shards)} shard(s), ~{total:.1f} MB\n")
    for shard, (name, _) in shards.items():
        download(shard, name)
    print("\ndone.")


if __name__ == "__main__":
    main()
