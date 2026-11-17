"""End-to-end CLI tests: run the real script the way a new user would."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "recommend_investors.py"
EXAMPLE_PROJECT = ROOT / "references" / "example-project.json"
FIXTURE = ROOT / "fixtures" / "synthetic-replay.json"


def run_cli(*args: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )


class CliEndToEndTests(unittest.TestCase):
    def test_self_test_passes(self) -> None:
        result = run_cli("--self-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stderr)

    def test_dry_run_makes_no_network_call_and_plans_queries(self) -> None:
        result = run_cli("--project", str(EXAMPLE_PROJECT), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "dry_run")
        names = [item["name"] for item in payload["query_passes"]]
        self.assertEqual(
            names,
            ["strict", "fit_without_compliance", "discovery_unknown_ticket"],
        )
        sectors = payload["query_passes"][0]["params"]["sector_focus"]
        self.assertIn("AI", sectors)
        self.assertIn("人工智能", sectors)

    def test_fixture_mode_runs_full_pipeline_offline(self) -> None:
        result = run_cli(
            "--project",
            str(EXAMPLE_PROJECT),
            "--fixture",
            str(FIXTURE),
            "--top-k",
            "10",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data_source"]["mode"], "fixture")
        recommendations = payload["recommendations"]
        self.assertGreater(len(recommendations), 0)
        ids = [item["investor"]["id"] for item in recommendations]
        tiers = {item["investor"]["id"]: item["recommendation_tier"] for item in recommendations}
        self.assertEqual(tiers.get("I-CORE-1"), "core")
        self.assertNotIn("I-RISK", ids, "risk candidate must be excluded")
        self.assertNotIn("I-OUT", ids, "non-overlapping ticket must be excluded")
        self.assertIn("verify", tiers.values(), "suspect candidate must land in verify")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("synthetic-core@example.invalid", serialized)
        self.assertNotIn("13800138000", serialized)

    def test_live_mode_without_credentials_fails_closed(self) -> None:
        result = run_cli(
            "--project",
            str(EXAMPLE_PROJECT),
            env_overrides={
                "INVESTOR_API_CLIENT_ID": "",
                "INVESTOR_API_CLIENT_SECRET": "",
            },
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "needs_input")
        self.assertIn("INVESTOR_API_CLIENT", payload["error"]["message"])
        self.assertEqual(payload["recommendations"], [])

    def test_invalid_input_is_rejected_before_network(self) -> None:
        bad_project = ROOT / "tests" / "_bad_project.json"
        bad_project.write_text(
            json.dumps(
                {
                    "project": {
                        "summary": "x",
                        "sectors": ["AI"],
                        "amount": {
                            "min_wan_cny": 2000,
                            "max_wan_cny": 1000,
                            "currency": "人民币",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        try:
            result = run_cli("--project", str(bad_project), "--dry-run")
        finally:
            bad_project.unlink()
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "needs_input")


if __name__ == "__main__":
    unittest.main()
