"""Parse the raw Houston 311 files, standardize them, and build the clean DuckDB tables.

- Skips each file's banner lines and reads the pipe-separated rows.
- Keeps only the columns in config.yaml (free-text notes are dropped).
- Converts every timestamp to UTC so durations are correct across DST changes.
- Loads `raw_311` into DuckDB, then runs sql/01_clean.sql to build `requests`.

Usage:
    python src/load_db.py                          # all files in data/raw/
    python src/load_db.py --files data/sample/houston_311_sample.txt
"""
from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path

import duckdb
import pandas as pd
import yaml

log = logging.getLogger("load_db")
ROOT = Path(__file__).resolve().parents[1]   # project folder (one level above src/)


def read_city_file(path: Path, banner_lines: int, wanted: dict[str, str]) -> pd.DataFrame:
    """Read one city file. Free-text columns at the end can contain '|', so each line is
    split only as far as the last column we need."""
    with open(path, encoding="utf-8", errors="replace") as f:
        # The file starts with a decorative banner (dashes, title, extract date).
        # We keep the extract date: it's the moment this snapshot was taken, so a
        # request still open in the file was open at THAT time, not today.
        banner = [next(f) for _ in range(banner_lines)]
        m = re.search(r"Extract date-time:\s*\[([^\]]+)\]", "".join(banner))
        extracted = m.group(1).strip() if m else None
        if extracted is None:
            log.warning("%s: no extract date in banner - open requests will be judged against today", path.name)
        # The next line is the header: column names separated by "|".
        header = [h.strip() for h in next(f).rstrip("\r\n").split("|")]
        # Stop early with a clear message if the city renamed a column.
        missing = [src for src in wanted.values() if src not in header]
        if missing:
            raise SystemExit(f"{path.name}: columns not found {missing}. Header is: {header}")
        # Position of each column we want, e.g. {"status": 5, "title": 8, ...}
        idx = {std: header.index(src) for std, src in wanted.items()}
        last = max(idx.values())
        rows, skipped = [], 0
        for line in f:
            # Split only up to the last column we need. The free-text notes after it
            # sometimes contain "|" themselves, which would shift every column.
            parts = line.rstrip("\r\n").split("|", last + 1)
            if len(parts) <= last:
                skipped += 1           # trailer lines or truncated rows
                continue
            rows.append([parts[i].strip() for i in idx.values()])
    log.info("%s: %d rows read, %d short lines skipped (extracted %s)", path.name, len(rows), skipped, extracted)
    # Empty strings become proper missing values (None) so SQL treats them as NULL.
    df = pd.DataFrame(rows, columns=list(idx.keys())).replace("", None)
    df["extracted_local"] = extracted          # same snapshot time for every row in this file
    return df


def to_utc(series: pd.Series, local_tz: str | None = None) -> pd.Series:
    """Turn text into timestamps. If the text is in local time, convert it to UTC.

    Houston changes clocks for daylight saving time. Comparing a local time with a
    UTC time would be off by 5-6 hours, so everything is stored in UTC.
    """
    ts = pd.to_datetime(series, errors="coerce")     # unreadable dates become NaT (missing)
    if local_tz:
        ts = (ts.dt.tz_localize(local_tz, ambiguous="NaT", nonexistent="shift_forward")
                .dt.tz_convert("UTC").dt.tz_localize(None))
    return ts


def standardize(df: pd.DataFrame, local_tz: str) -> pd.DataFrame:
    """Convert the raw text columns into usable types."""
    out = df.copy()
    # Time zones checked against real rows: created and due dates are already UTC,
    # but the closed date is Houston local time.
    out["created_at"] = to_utc(out.pop("created_utc"))
    out["due_at"] = to_utc(out.pop("due_utc"))
    out["closed_at"] = to_utc(out.pop("closed_local"), local_tz)
    # The banner's extract time is Houston local time (checked against a live file).
    out["extract_at"] = to_utc(out.pop("extracted_local"), local_tz)
    # "30 Calendar Days" -> sla_days = 30, sla_unit = "Calendar"
    sla = out["sla"].fillna("")
    out["sla_days"] = pd.to_numeric(sla.str.extract(r"(\d+(?:\.\d+)?)")[0], errors="coerce")
    out["sla_unit"] = sla.str.extract(r"(Calendar|Business|Hour|Minute)", flags=re.I)[0].str.title()
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    p = argparse.ArgumentParser()
    p.add_argument("--files", nargs="*", help="files to load (default: every .txt in data/raw)")
    args = p.parse_args()

    # Which files to read: the ones given with --files, or everything downloaded.
    files = [ROOT / f for f in args.files] if args.files else sorted((ROOT / cfg["paths"]["raw_dir"]).glob("*.txt"))
    if not files:
        raise SystemExit("No data files found - run `python src/download.py` first.")

    # Read every file, stack them into one table, and fix the types.
    frames = [read_city_file(f, cfg["source"]["banner_lines"], cfg["columns"]) for f in files]
    df = standardize(pd.concat(frames, ignore_index=True), cfg["source"]["local_timezone"])
    log.info("Combined: %d rows", len(df))

    # Save into DuckDB: a database that lives in a single file, no server needed.
    db_path = ROOT / cfg["paths"]["database"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.register("df", df)                                         # let SQL see the pandas table
    con.execute("CREATE OR REPLACE TABLE raw_311 AS SELECT * FROM df")
    # Run the cleaning SQL, filling in the list of "not finished" statuses from config.
    statuses = ", ".join(f"'{s}'" for s in cfg["not_finished_statuses"]) or "''"
    con.execute((ROOT / "sql" / "01_clean.sql").read_text().replace("{NOT_FINISHED_STATUSES}", statuses))

    # Quick sanity check printed to the screen.
    n_raw = con.execute("SELECT COUNT(*) FROM raw_311").fetchone()[0]
    n, n_label, rate = con.execute(
        "SELECT COUNT(*), COUNT(is_overdue), AVG(is_overdue::INT) FROM requests").fetchone()
    log.info("raw_311: %d rows -> requests: %d rows, %d labeled, overdue rate %.1f%%",
             n_raw, n, n_label, 100 * (rate or 0))
    con.close()


if __name__ == "__main__":
    main()
