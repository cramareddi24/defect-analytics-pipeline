"""
Defect Analytics Pipeline

Takes a raw defect log export, cleans it, and produces the metrics I'd
want in front of a release review: what's still open, what's critical,
where defects come from, and how fast they get fixed.

Usage:
    python src/pipeline.py
    python src/pipeline.py --input data/raw/defect_log.csv --output outputs
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SEVERITY_ORDER = ["Critical", "High", "Medium", "Low"]
OPEN_STATUSES = {"Open", "In Progress", "Reopened"}
DONE_STATUSES = {"Resolved", "Closed"}

# The one correct spelling for every categorical value
CANONICAL_VALUES = {
    "module": ["Authentication", "Dashboard", "Reports", "Client Profiles", "Scheduling",
               "Billing", "Notifications", "Admin Console"],
    "user_role": ["Admin", "Manager", "Standard User", "Read-Only"],
    "environment": ["Dev", "Staging", "Production"],
    "severity": SEVERITY_ORDER,
    "priority": ["P1", "P2", "P3", "P4"],
    "status": ["Open", "In Progress", "Reopened", "Resolved", "Closed"],
    "root_cause": ["Requirements Gap", "UI Logic", "API Validation", "Data Integrity",
                   "Environment Config", "Third-Party Integration"],
}


def canonicalize(series: pd.Series, allowed: list[str]) -> tuple[pd.Series, set[str]]:
    """Case and whitespace insensitive match against a list of allowed values."""
    lookup = {v.lower(): v for v in allowed}
    stripped = series.astype("string").str.strip()
    mapped = stripped.str.lower().map(lookup)
    unknown = set(stripped[mapped.isna() & stripped.notna()].unique())
    return mapped.fillna(stripped), unknown


# ---------------------------------------------------------------------------
# 1. Clean
# ---------------------------------------------------------------------------
def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Standardize the raw export and return (clean_df, data_quality_notes)."""
    df = raw.copy()
    notes = []

    # Map messy values ("  dashboard", "HIGH ", "in progress") to one canonical spelling
    for col, allowed in CANONICAL_VALUES.items():
        df[col], unknown = canonicalize(df[col], allowed)
        if unknown:
            notes.append(f"Unrecognized {col} values left as-is: {', '.join(sorted(unknown))}.")

    # Remove duplicate tickets from double exports
    before = len(df)
    df = df.drop_duplicates(subset="defect_id", keep="first")
    removed = before - len(df)
    if removed:
        notes.append(f"Removed {removed} duplicate rows (same defect_id exported twice).")

    # Parse dates
    df["date_reported"] = pd.to_datetime(df["date_reported"], errors="coerce")
    df["date_resolved"] = pd.to_datetime(df["date_resolved"], errors="coerce")

    # Flag tickets marked done with no resolution date
    missing_dates = df["status"].isin(DONE_STATUSES) & df["date_resolved"].isna()
    if missing_dates.any():
        ids = ", ".join(sorted(df.loc[missing_dates, "defect_id"].tolist()))
        notes.append(
            f"{missing_dates.sum()} tickets are Resolved/Closed but have no resolution date ({ids}). "
            "Excluded from time-to-resolve metrics."
        )

    # Flag impossible date order
    bad_order = df["date_resolved"] < df["date_reported"]
    if bad_order.any():
        notes.append(f"{bad_order.sum()} tickets have a resolution date before the report date.")

    df["days_to_resolve"] = (df["date_resolved"] - df["date_reported"]).dt.days
    df["is_open"] = df["status"].isin(OPEN_STATUSES)
    df["severity"] = pd.Categorical(df["severity"], categories=SEVERITY_ORDER, ordered=True)

    return df.sort_values("defect_id").reset_index(drop=True), notes


# ---------------------------------------------------------------------------
# 2. Analyze
# ---------------------------------------------------------------------------
def headline_metrics(df: pd.DataFrame) -> dict:
    total = len(df)
    open_df = df[df["is_open"]]
    return {
        "Total defects": total,
        "Open (Open / In Progress / Reopened)": len(open_df),
        "Open Critical + High": int(open_df["severity"].isin(["Critical", "High"]).sum()),
        "Found in Production (escape rate)": f"{(df['environment'] == 'Production').mean():.1%}",
        "Reopen rate": f"{(df['reopened_count'] > 0).mean():.1%}",
        "Median days to resolve": f"{df['days_to_resolve'].median():.1f}",
    }


def status_by_severity(df: pd.DataFrame) -> pd.DataFrame:
    table = pd.crosstab(df["severity"], df["status"], margins=True, margins_name="Total")
    col_order = [c for c in ["Open", "In Progress", "Reopened", "Resolved", "Closed", "Total"] if c in table.columns]
    return table[col_order]


def resolution_time_by_severity(df: pd.DataFrame) -> pd.DataFrame:
    resolved = df.dropna(subset=["days_to_resolve"])
    out = resolved.groupby("severity", observed=True)["days_to_resolve"].agg(["count", "mean", "median", "max"])
    out.columns = ["Resolved count", "Mean days", "Median days", "Max days"]
    return out.round(1)


def root_cause_pareto(df: pd.DataFrame) -> pd.DataFrame:
    counts = df["root_cause"].value_counts()
    out = pd.DataFrame({"Defects": counts})
    out["Share"] = out["Defects"] / out["Defects"].sum()
    out["Cumulative share"] = out["Share"].cumsum()
    return out


def module_severity_matrix(df: pd.DataFrame) -> pd.DataFrame:
    table = pd.crosstab(df["module"], df["severity"])
    table["Total"] = table.sum(axis=1)
    return table.sort_values("Total", ascending=False)


def weekly_trend(df: pd.DataFrame) -> pd.DataFrame:
    opened = df.set_index("date_reported").resample("W-MON")["defect_id"].count().rename("Opened")
    closed = (
        df.dropna(subset=["date_resolved"]).set_index("date_resolved").resample("W-MON")["defect_id"].count().rename("Resolved")
    )
    trend = pd.concat([opened, closed], axis=1).fillna(0).astype(int)
    trend["Net open change"] = trend["Opened"] - trend["Resolved"]
    trend["Running backlog"] = trend["Net open change"].cumsum()
    return trend


# ---------------------------------------------------------------------------
# 3. Visualize
# ---------------------------------------------------------------------------
def save_charts(df: pd.DataFrame, pareto: pd.DataFrame, mttr: pd.DataFrame, trend: pd.DataFrame, chart_dir: Path) -> None:
    chart_dir.mkdir(parents=True, exist_ok=True)

    # Root cause Pareto
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(pareto.index, pareto["Defects"], color="#3b6ea5")
    ax.set_ylabel("Defects")
    ax.set_title("Defects by Root Cause (Pareto)")
    ax.tick_params(axis="x", rotation=25)
    ax2 = ax.twinx()
    ax2.plot(pareto.index, pareto["Cumulative share"] * 100, color="#d1495b", marker="o")
    ax2.set_ylabel("Cumulative %")
    ax2.set_ylim(0, 105)
    fig.tight_layout()
    fig.savefig(chart_dir / "root_cause_pareto.png", dpi=150)
    plt.close(fig)

    # Mean days to resolve by severity
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.bar(mttr.index.astype(str), mttr["Mean days"], color=["#d1495b", "#edae49", "#3b6ea5", "#66a182"])
    ax.set_ylabel("Mean days to resolve")
    ax.set_title("Time to Resolve by Severity")
    for i, v in enumerate(mttr["Mean days"]):
        ax.text(i, v + 0.2, f"{v:.1f}", ha="center")
    fig.tight_layout()
    fig.savefig(chart_dir / "time_to_resolve_by_severity.png", dpi=150)
    plt.close(fig)

    # Weekly opened vs resolved
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(trend.index, trend["Opened"], label="Opened", marker="o", color="#d1495b")
    ax.plot(trend.index, trend["Resolved"], label="Resolved", marker="o", color="#66a182")
    ax.set_title("Weekly Defects Opened vs Resolved")
    ax.set_ylabel("Defects")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(chart_dir / "weekly_opened_vs_resolved.png", dpi=150)
    plt.close(fig)

    # Module x severity stacked bar
    matrix = pd.crosstab(df["module"], df["severity"])
    matrix = matrix.loc[matrix.sum(axis=1).sort_values().index]
    fig, ax = plt.subplots(figsize=(9, 5))
    matrix.plot(kind="barh", stacked=True, ax=ax, color=["#d1495b", "#edae49", "#3b6ea5", "#66a182"])
    ax.set_xlabel("Defects")
    ax.set_ylabel("")
    ax.set_title("Defects by Module and Severity")
    fig.tight_layout()
    fig.savefig(chart_dir / "module_by_severity.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------
def build_takeaways(df: pd.DataFrame, pareto: pd.DataFrame, matrix: pd.DataFrame, mttr: pd.DataFrame) -> list[str]:
    top_two = pareto.head(2)
    top_two_share = top_two["Share"].sum()
    ch = matrix[["Critical", "High"]].sum(axis=1).sort_values(ascending=False)
    open_crit = df[df["is_open"] & (df["severity"] == "Critical")]
    prod_crit = df[(df["environment"] == "Production") & df["severity"].isin(["Critical", "High"])]

    return [
        f"**{top_two.index[0]}** and **{top_two.index[1]}** account for {top_two_share:.0%} of all defects. "
        "Putting prevention effort (earlier test cases, clearer specs) into these two areas addresses the biggest share of rework.",
        f"**{ch.index[0]}** has the most Critical/High defects ({ch.iloc[0]}), followed by **{ch.index[1]}** ({ch.iloc[1]}). "
        "These are the modules to prioritize for regression coverage.",
        f"{len(open_crit)} Critical defects are still open. These should be release blockers until closed or explicitly accepted.",
        f"{len(prod_crit)} Critical/High defects were found in Production, meaning they escaped Dev and Staging testing.",
        f"Critical defects resolve in {mttr.loc['Critical', 'Mean days']:.1f} days on average vs "
        f"{mttr.loc['Low', 'Mean days']:.1f} for Low, so triage by severity is working as intended.",
    ]


def df_to_md(df: pd.DataFrame, pct_cols: tuple = ()) -> str:
    out = df.copy()
    for col in pct_cols:
        out[col] = out[col].map(lambda x: f"{x:.1%}")
    return out.to_markdown()


def write_report(df, notes, metrics, status_tbl, mttr, pareto, matrix, trend, out_dir: Path) -> Path:
    lines = ["# Defect Analytics Summary", ""]
    lines.append(
        f"Reporting window: {df['date_reported'].min():%b %d, %Y} to {df['date_reported'].max():%b %d, %Y}. "
        "Generated automatically by `src/pipeline.py` from synthetic data."
    )
    lines += ["", "## Headline metrics", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in metrics.items()]

    lines += ["", "## Key takeaways", ""]
    lines += [f"- {t}" for t in build_takeaways(df, pareto, matrix, mttr)]

    lines += ["", "## Data quality notes", ""]
    lines += [f"- {n}" for n in notes] if notes else ["- No issues found."]

    lines += ["", "## Status by severity", "", status_tbl.to_markdown()]
    lines += ["", "## Time to resolve by severity (days)", "", mttr.to_markdown()]
    lines += ["", "## Root cause Pareto", "", df_to_md(pareto, pct_cols=("Share", "Cumulative share"))]
    lines += ["", "## Defects by module and severity", "", matrix.to_markdown()]
    lines += ["", "## Charts", ""]
    for name, title in [
        ("root_cause_pareto.png", "Root cause Pareto"),
        ("module_by_severity.png", "Module by severity"),
        ("time_to_resolve_by_severity.png", "Time to resolve"),
        ("weekly_opened_vs_resolved.png", "Weekly trend"),
    ]:
        lines.append(f"![{title}](charts/{name})")

    path = out_dir / "summary_report.md"
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(input_path: Path, out_dir: Path) -> None:
    raw = pd.read_csv(input_path)
    df, notes = clean(raw)

    metrics = headline_metrics(df)
    status_tbl = status_by_severity(df)
    mttr = resolution_time_by_severity(df)
    pareto = root_cause_pareto(df)
    matrix = module_severity_matrix(df)
    trend = weekly_trend(df)

    out_dir.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["is_open"]).to_csv(out_dir / "defect_log_clean.csv", index=False)
    trend.to_csv(out_dir / "weekly_trend.csv")
    save_charts(df, pareto, mttr, trend, out_dir / "charts")
    report = write_report(df, notes, metrics, status_tbl, mttr, pareto, matrix, trend, out_dir)

    print(f"Raw rows: {len(raw)} | Clean rows: {len(df)}")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"Report written to {report}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean and analyze a defect log export.")
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "raw" / "defect_log.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs")
    args = parser.parse_args()
    run(args.input, args.output)


if __name__ == "__main__":
    main()
