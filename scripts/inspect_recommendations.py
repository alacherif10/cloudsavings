"""Dev tool: pretty-print recommendation engine output on demo data.

Usage (from project root, venv active):

    python scripts\\inspect_recommendations.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# When this file is run directly, Python puts `scripts/` on sys.path,
# not the project root. Add the project root explicitly so `src` can
# be imported.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.csv_import import import_csv_from_bytes  # noqa: E402
from src.recommendations import generate_recommendations  # noqa: E402

DEMO_PATH = PROJECT_ROOT / "data" / "demo_costs.csv"


def main() -> None:
    df = import_csv_from_bytes(DEMO_PATH.read_bytes(), DEMO_PATH.name).df
    assert df is not None

    recs = generate_recommendations(df)

    print(f"\n{len(recs)} recommendation(s)\n")
    for i, r in enumerate(recs, 1):
        print(f"{i}. [{r.confidence.upper()}] {r.title}")
        print(f"   id:                {r.id}")
        print(f"   service:           {r.service}")
        print(f"   potential saving:  ${r.estimated_monthly_savings_usd:,.2f}/month")
        print(f"   next action:       {r.next_action}")
        print()


if __name__ == "__main__":
    main()