"""The deployed workflow must refresh a same-day snapshot from an older classifier."""
import contextlib
import datetime
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_heartbeat as base

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/tw-warrant-heartbeat-probe.yml"
source = WORKFLOW.read_text(encoding="utf-8")
precheck = source.split("          current=$(python - <<'PY'\n", 1)[1].split("          PY\n", 1)[0]
SCRIPT = "\n".join(line[10:] for line in precheck.splitlines())


class FrozenDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat("2026-10-11T10:00:00+08:00").astimezone(tz)


class SnapshotPrecheckTests(unittest.TestCase):
    def snapshot(self):
        return {
            "pipeline": {"execution_date": "2026-10-11",
                         "all_market_pure_warrant_top100_certifiable": True,
                         "final_count_certifiable": True},
            "data_status": {market: {"product_classification": {"policy": base.PRODUCT_CLASSIFICATION_POLICY}}
                            for market in ("twse", "tpex")},
        }

    def evaluate(self, snapshot, exists=True):
        output = io.StringIO()
        with patch.object(Path, "exists", return_value=exists), \
             patch.object(Path, "read_text", return_value=json.dumps(snapshot)), \
             patch.object(datetime, "datetime", FrozenDateTime), contextlib.redirect_stdout(output):
            exec(compile(SCRIPT, str(WORKFLOW), "exec"), {})
        return output.getvalue().strip() == "true"

    def test_same_day_unclassified_snapshot_rebuilds(self):
        snapshot = self.snapshot()
        snapshot.pop("data_status")
        self.assertFalse(self.evaluate(snapshot))

    def test_current_classification_in_both_markets_can_skip_rebuild(self):
        self.assertTrue(self.evaluate(self.snapshot()))

    def test_missing_or_old_policy_in_either_market_rebuilds(self):
        for market in ("twse", "tpex"):
            for policy in (None, "old-policy"):
                snapshot = self.snapshot()
                snapshot["data_status"][market]["product_classification"]["policy"] = policy
                with self.subTest(market=market, policy=policy):
                    self.assertFalse(self.evaluate(snapshot))

    def test_prior_day_snapshot_still_rebuilds(self):
        snapshot = self.snapshot()
        snapshot["pipeline"]["execution_date"] = "2026-10-10"
        self.assertFalse(self.evaluate(snapshot))

    def test_uncertified_snapshot_still_rebuilds(self):
        for field in ("all_market_pure_warrant_top100_certifiable", "final_count_certifiable"):
            snapshot = self.snapshot()
            snapshot["pipeline"][field] = False
            self.assertFalse(self.evaluate(snapshot))

    def test_missing_snapshot_rebuilds(self):
        self.assertFalse(self.evaluate(self.snapshot(), exists=False))


if __name__ == "__main__":
    unittest.main()
