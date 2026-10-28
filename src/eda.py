"""Run the exploration queries in sql/02_eda.sql and save tables + charts to reports/.

Usage:
    python src/eda.py
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

log = logging.getLogger("eda")
ROOT = Path(__file__).resolve().parents[1]


def named_queries(sql_text: str) -> dict[str, str]:
    parts = re.split(r"^-- name:\s*(\w+)\s*$", sql_text, flags=re.M)
    return {parts[i]: parts[i + 1].strip().rstrip(";") for i in range(1, len(parts), 2)}


def bar(df, x, y, title, path, horizontal=True):
    fig, ax = plt.subplots(figsize=(8, 5))
    data = df.dropna(subset=[x]).copy()
    if horizontal:
        data = data.iloc[::-1]
        ax.barh(data[x].astype(str), data[y], color="#3b6ea5")
        ax.set_xlabel(y.replace("_", " "))
    else:
        ax.bar(data[x].astype(str), data[y], color="#3b6ea5")
        ax.set_ylabel(y.replace("_", " "))
        plt.xticks(rotation=45, ha="right")
    ax.set_title(title)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    reports = ROOT / cfg["paths"]["reports"]
    reports.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(ROOT / cfg["paths"]["database"]), read_only=True)

    results = {}
    for name, query in named_queries((ROOT / "sql" / "02_eda.sql").read_text()).items():
        df = con.execute(query).df()
        df.to_csv(reports / f"{name}.csv", index=False)
        results[name] = df
        log.info("%s: %d rows", name, len(df))
    con.close()

    print("\n=== Overview ===")
    print(results["overview"].T.to_string(header=False))

    bar(results["top_case_types"], "case_type", "requests",
        "Most common 311 request types", reports / "top_case_types.png")
    bar(results["by_department"], "department", "pct_overdue",
        "Share of requests closed late, by department (%)", reports / "overdue_by_department.png")
    bar(results["by_district"], "district", "median_days",
        "Median days to close, by council district", reports / "median_days_by_district.png",
        horizontal=False)

    trend = results["monthly_trend"]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(trend["created_month"], trend["requests"], color="#3b6ea5", marker="o")
    ax.set_title("311 requests per month")
    ax.set_ylabel("requests")
    ax.spines[["top", "right"]].set_visible(False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(reports / "monthly_trend.png", dpi=150)
    plt.close(fig)
    log.info("Saved tables and charts to %s", reports)


if __name__ == "__main__":
    main()
