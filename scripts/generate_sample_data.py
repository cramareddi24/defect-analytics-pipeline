"""
Generates a synthetic defect log for a fictional SaaS web app.

The data is intentionally a little messy (inconsistent casing, extra
whitespace, duplicate rows, missing dates) because real defect trackers
look like this, and cleaning it is part of the job.

No real company, product, or client data is used.

Usage:
    python scripts/generate_sample_data.py
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SEED = 42
N_DEFECTS = 260
START_DATE = date(2025, 1, 6)
END_DATE = date(2025, 6, 27)

MODULES = [
    "Authentication",
    "Dashboard",
    "Reports",
    "Client Profiles",
    "Scheduling",
    "Billing",
    "Notifications",
    "Admin Console",
]
USER_ROLES = ["Admin", "Manager", "Standard User", "Read-Only"]
ENVIRONMENTS = ["Dev", "Staging", "Production"]
SEVERITIES = ["Critical", "High", "Medium", "Low"]
STATUSES = ["Closed", "Resolved", "In Progress", "Open", "Reopened"]
ROOT_CAUSES = [
    "Requirements Gap",
    "UI Logic",
    "API Validation",
    "Data Integrity",
    "Environment Config",
    "Third-Party Integration",
]

# How long each severity usually takes to fix (min, max days)
RESOLUTION_DAYS = {
    "Critical": (1, 4),
    "High": (2, 9),
    "Medium": (3, 16),
    "Low": (5, 28),
}
PRIORITY_BY_SEVERITY = {"Critical": "P1", "High": "P2", "Medium": "P3", "Low": "P4"}


def random_date(rng: random.Random) -> date:
    span = (END_DATE - START_DATE).days
    return START_DATE + timedelta(days=rng.randint(0, span))


def build_clean_rows(rng: random.Random) -> list[dict]:
    rows = []
    for i in range(1, N_DEFECTS + 1):
        module = rng.choices(MODULES, weights=[10, 18, 16, 14, 11, 12, 8, 11])[0]
        severity = rng.choices(SEVERITIES, weights=[7, 23, 45, 25])[0]
        status = rng.choices(STATUSES, weights=[55, 15, 12, 13, 5])[0]
        root_cause = rng.choices(ROOT_CAUSES, weights=[24, 20, 19, 16, 12, 9])[0]
        reported = random_date(rng)

        resolved = None
        if status in ("Closed", "Resolved"):
            lo, hi = RESOLUTION_DAYS[severity]
            resolved = reported + timedelta(days=rng.randint(lo, hi))

        rows.append(
            {
                "defect_id": f"DEF-{i:04d}",
                "date_reported": reported.isoformat(),
                "module": module,
                "feature_id": f"F-{rng.randint(1, 120):03d}",
                "user_role": rng.choice(USER_ROLES),
                "environment": rng.choices(ENVIRONMENTS, weights=[35, 50, 15])[0],
                "severity": severity,
                "priority": PRIORITY_BY_SEVERITY[severity],
                "status": status,
                "root_cause": root_cause,
                "date_resolved": resolved.isoformat() if resolved else "",
                "reopened_count": 1 if status == "Reopened" else rng.choices([0, 1], weights=[92, 8])[0],
            }
        )
    return rows


def add_messiness(df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
    """Introduce the kind of inconsistencies you see in a shared tracker."""
    df = df.copy()

    # Inconsistent casing and stray whitespace
    for idx in rng.sample(range(len(df)), 25):
        df.loc[idx, "severity"] = rng.choice([df.loc[idx, "severity"].lower(), df.loc[idx, "severity"].upper() + " "])
    for idx in rng.sample(range(len(df)), 20):
        df.loc[idx, "module"] = "  " + df.loc[idx, "module"].lower()
    for idx in rng.sample(range(len(df)), 10):
        df.loc[idx, "status"] = df.loc[idx, "status"].lower()

    # A few closed tickets with no resolution date (common tracker gap)
    closed_idx = df.index[df["status"] == "Closed"].tolist()
    for idx in rng.sample(closed_idx, 4):
        df.loc[idx, "date_resolved"] = ""

    # Duplicate rows from a double export
    dupes = df.sample(n=8, random_state=SEED)
    df = pd.concat([df, dupes], ignore_index=True)

    return df.sample(frac=1, random_state=SEED).reset_index(drop=True)


def main() -> None:
    rng = random.Random(SEED)
    df = pd.DataFrame(build_clean_rows(rng))
    df = add_messiness(df, rng)

    out_path = Path(__file__).resolve().parents[1] / "data" / "raw" / "defect_log.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")


if __name__ == "__main__":
    main()
