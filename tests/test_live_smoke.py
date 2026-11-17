"""Optional live smoke test: screening constraints on real API data.

Runs only when credentials are configured (environment variables or the skill
root .env). Without credentials every test is skipped, so CI stays offline.
Keep the request volume small: one page per filter check.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "recommend_investors_live_smoke", ROOT / "scripts" / "recommend_investors.py"
)
r = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = r
_SPEC.loader.exec_module(r)
r.load_skill_env()

HAS_CREDENTIALS = bool(
    os.getenv("INVESTOR_API_CLIENT_ID") and os.getenv("INVESTOR_API_CLIENT_SECRET")
)

BASE = {
    "sector_focus": "人工智能,AI,A.I.,Artificial Intelligence,人工智能/机器学习,人工智慧",
    "include": "research,compliance",
    "order_by": "updated_at_desc",
}


@unittest.skipUnless(HAS_CREDENTIALS, "live API credentials not configured")
class LiveScreeningSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.api = r.InvestorApi()

    def test_compliance_filter_is_enforced_by_server(self) -> None:
        result = self.api.fetch_pages({**BASE, "compliance_overall": "trusted"}, max_pages=1)
        self.assertGreater(result.unique, 0, "trusted filter returned no rows; cannot verify")
        bad = [
            item.get("id")
            for item in result.items
            if str(item.get("compliance_overall") or "").lower() != "trusted"
        ]
        self.assertEqual(bad, [], "server returned non-trusted rows for a trusted query")

    def test_amount_filter_is_enforced_by_server(self) -> None:
        result = self.api.fetch_pages({**BASE, "min_amount": 1000, "max_amount": 2000}, max_pages=1)
        self.assertGreater(result.unique, 0, "amount filter returned no rows; cannot verify")
        bad = []
        for item in result.items:
            inv_min = r.parse_amount(item.get("inv_min"))
            inv_max = r.parse_amount(item.get("inv_max"))
            if inv_min is None or inv_max is None:
                bad.append((item.get("id"), "unknown ticket returned for amount query"))
            elif not r.ranges_overlap(inv_min, inv_max, 1000, 2000):
                bad.append((item.get("id"), f"{inv_min}-{inv_max} does not overlap"))
        self.assertEqual(bad, [], "server returned non-overlapping rows for an amount query")

    def test_recommendations_respect_hard_rules(self) -> None:
        profile = r.normalize_project(
            {
                "project": {
                    "summary": "工业人工智能质检与设备预测性维护软件",
                    "sectors": ["人工智能"],
                    "amount": {"min_wan_cny": 500, "max_wan_cny": 5000, "currency": "人民币"},
                },
                "preferences": {"compliance": ["trusted"]},
                "top_k": 10,
            }
        )
        result = r.recommend(profile, self.api, top_k=10, max_pages=1, max_per_fund=3)
        recommendations = result["recommendations"]
        self.assertGreater(len(recommendations), 0, "live query returned nothing; cannot verify")

        fund_counts: dict[str, int] = {}
        previous_key = (-1, float("-inf"))
        for item in recommendations:
            compliance = str(item["data_quality"].get("compliance_overall") or "").lower()
            self.assertNotEqual(compliance, "risk", f"{item['investor']['id']} is risk")
            fund_key = str(item["fund"]["id"] or item["fund"]["name"])
            fund_counts[fund_key] = fund_counts.get(fund_key, 0) + 1

            tier_rank = 0 if item["recommendation_tier"] == "core" else 1
            sort_key = (tier_rank, -float(item["score"]["total"]))
            self.assertGreaterEqual(sort_key, previous_key, "results are not sorted deterministically")
            previous_key = sort_key

            if item["recommendation_tier"] == "core":
                self.assertEqual(compliance, "trusted", "core candidate must be trusted")
                ticket = item["investment_range_wan_cny"]
                self.assertIsNotNone(ticket["min"], "core candidate must have a known ticket")
                self.assertIsNotNone(ticket["max"], "core candidate must have a known ticket")
                self.assertTrue(
                    r.ranges_overlap(ticket["min"], ticket["max"], 500, 5000),
                    "core candidate ticket must overlap the ask",
                )

        self.assertTrue(
            all(count <= 3 for count in fund_counts.values()),
            f"per-fund cap violated: {fund_counts}",
        )


if __name__ == "__main__":
    unittest.main()
