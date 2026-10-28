"""Download Houston 311 yearly data files listed in config.yaml.

Files are large (250-420 MB each), so they are streamed to disk with a progress log.
Already-downloaded files are skipped unless you pass --force.

Usage:
    python src/download.py
    python src/download.py --peek      # print the banner + header of each file and stop
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import requests
import yaml

log = logging.getLogger("download")
ROOT = Path(__file__).resolve().parents[1]


def peek(url: str) -> None:
    r = requests.get(url, headers={"Range": "bytes=0-3000"}, timeout=60)
    r.raise_for_status()
    total = r.headers.get("Content-Range", "?").split("/")[-1]
    print(f"\n{url}\n  size: {int(total) / 1e6:.0f} MB" if total.isdigit() else f"\n{url}")
    for line in r.text.splitlines()[:6]:
        print("  " + line[:300])


def download(url: str, dest: Path, force: bool) -> None:
    if dest.exists() and not force:
        log.info("Already have %s (%.0f MB) - skipping", dest.name, dest.stat().st_size / 1e6)
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        done, next_log = 0, 0.0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total and done / total >= next_log:
                    log.info("%s: %3.0f%% (%.0f / %.0f MB)", dest.name, 100 * done / total, done / 1e6, total / 1e6)
                    next_log += 0.1
    tmp.rename(dest)
    log.info("Saved %s", dest)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    p = argparse.ArgumentParser()
    p.add_argument("--peek", action="store_true", help="show the header of each file and stop")
    p.add_argument("--force", action="store_true", help="re-download files that already exist")
    args = p.parse_args()

    raw_dir = ROOT / cfg["paths"]["raw_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name in cfg["source"]["files"]:
        url = cfg["source"]["base_url"] + name
        if args.peek:
            peek(url)
        else:
            download(url, raw_dir / name, args.force)


if __name__ == "__main__":
    main()
