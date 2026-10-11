"""Offline regressions; run with unittest discover from this directory."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_heartbeat_v6 as v6

base = v6.base
v5 = v6.v5
FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/official_product_references.json").read_text(encoding="utf-8")
)
EXECUTION_DATE = date(2026, 10, 7)
CLOSE_DATE = date(2026, 10, 6)


class ProductClassificationTests(unittest.TestCase):
    def setUp(self):
        self.network = patch("urllib.request.urlopen", side_effect=AssertionError("offline test"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_real_bear_counterexamples_excluded_despite_put_label(self):
        for code in ("03737B", "03736B"):
            with self.subTest(code=code):
                ref = FIXTURES["twse"][code]
                self.assertEqual(ref["權證類型"], "認售")
                result = base.classify_warrant_reference("TWSE", ref)
                self.assertEqual(result["product_class"], "cbbc")
                self.assertIn("cbbc_code_suffix", result["reasons"])
                self.assertIn("cbbc_reference_category", result["reasons"])

    def test_real_plain_calls_puts_and_reset_warrants_remain(self):
        for market, section, keys in [
            ("TWSE", "twse", ("030001", "put", "reset")),
            ("TPEx", "tpex", ("call", "put")),
        ]:
            for key in keys:
                with self.subTest(market=market, key=key):
                    result = base.classify_warrant_reference(market, FIXTURES[section][key])
                    self.assertEqual(result["product_class"], "plain_warrant")

    def test_bull_and_extendable_bull_source_rows_excluded(self):
        for key in ("bull", "extend_bull"):
            self.assertEqual(
                base.classify_warrant_reference("TWSE", FIXTURES["twse"][key])["product_class"],
                "cbbc",
            )

    def test_code_rules_do_not_depend_on_two_hardcoded_counterexamples(self):
        for suffix in "CBXY":
            for market in ("TWSE", "TPEx"):
                ref = self.reference(market)
                ref["權證代號" if market == "TWSE" else "Code"] = "08888" + suffix
                with self.subTest(market=market, suffix=suffix):
                    self.assertEqual(base.classify_warrant_reference(market, ref)["product_class"], "cbbc")

    def test_all_documented_plain_put_suffixes_and_foreign_call(self):
        for suffix in "PUTQ":
            ref = {**FIXTURES["twse"]["put"], "權證代號": "08888" + suffix}
            self.assertEqual(base.classify_warrant_reference("TWSE", ref)["product_class"], "plain_warrant")
        ref = {**self.reference("TWSE"), "權證代號": "08888F"}
        self.assertEqual(base.classify_warrant_reference("TWSE", ref)["product_class"], "plain_warrant")

    def test_name_category_and_quote_veto_misleading_plain_code(self):
        variants = [
            ({"權證簡稱": "臺股指測試63熊16"}, None),
            ({"類別": "上限型；重設型-單一單價"}, None),
            ({}, "臺股指測試63牛01"),
        ]
        for changes, quote in variants:
            ref = {**self.reference("TWSE"), **changes}
            self.assertEqual(base.classify_warrant_reference("TWSE", ref, quote)["product_class"], "cbbc")

    def test_unknown_and_missing_fields_are_not_plain(self):
        for key in ("權證代號", "權證簡稱", "權證類型", "類別"):
            ref = self.reference("TWSE")
            ref.pop(key)
            with self.subTest(missing=key):
                self.assertEqual(base.classify_warrant_reference("TWSE", ref)["product_class"], "unknown")
        for changes in ({"權證代號": "08888Z"}, {"類別": "新型商品"}, {"權證類型": "未知"},
                        {"權證簡稱": "名稱無商品分類"}, {"權證代號": "08888P"}):
            ref = {**self.reference("TWSE"), **changes}
            self.assertEqual(base.classify_warrant_reference("TWSE", ref)["product_class"], "unknown")
        self.assertEqual(
            base.classify_warrant_reference("TWSE", self.reference("TWSE"), "未知商品")["product_class"],
            "unknown",
        )
        for key in ("Code", "Name", "Type"):
            ref = self.reference("TPEx")
            ref.pop(key)
            with self.subTest(market="TPEx", missing=key):
                self.assertEqual(base.classify_warrant_reference("TPEx", ref)["product_class"], "unknown")
        ref = {**self.reference("TPEx"), "Type": "認售"}
        result = base.classify_warrant_reference("TPEx", ref)
        self.assertEqual(result["product_class"], "unknown")
        self.assertIn("conflicting_product_direction", result["reasons"])

    def test_nfkc_and_underlying_bull_name_are_not_false_exclusions(self):
        ref = {**self.reference("TWSE"), "權證代號": "０３０００１", "權證簡稱": "金牛公司券商６Ａ購０１"}
        self.assertEqual(base.classify_warrant_reference("TWSE", ref)["product_class"], "plain_warrant")

    def test_tpex_positive_cap_or_floor_is_excluded(self):
        for field in ("CapPrice/Index", "FloorPrice/Index"):
            ref = {**self.reference("TPEx"), field: "100.00"}
            self.assertEqual(base.classify_warrant_reference("TPEx", ref)["product_class"], "cbbc")

    @staticmethod
    def reference(market):
        return copy.deepcopy(FIXTURES["twse"]["030001"] if market == "TWSE" else FIXTURES["tpex"]["call"])

    def build(self, market, references):
        status = {}
        if market == "TWSE":
            # Price is present so v4 never needs a historical network lookup.
            quotes = {r.get("權證代號"): {"name": r.get("權證簡稱"), "close_num": 1.0,
                       "volume_num": 100000, "value_num": 100000} for r in references}
            result = base.build_twse(EXECUTION_DATE, CLOSE_DATE, references, [], [],
                                    {"security_by_code": quotes, "security_by_name": {}, "index_by_name": {}}, status)
        else:
            quotes = [{"Code": r.get("Code"), "Name": r.get("Name"), "Date": "20261006",
                       "Close": "1", "UnderlyingStockClosePrice": "100", "TradeVol.": "100000"}
                      for r in references]
            result = base.build_tpex(EXECUTION_DATE, CLOSE_DATE, references, quotes, [], [], status)
        return result

    def test_live_and_v6_cache_fallback_have_identical_classification(self):
        for market, key, cache_name, reconcile in [
            ("TWSE", "twse_basic", "twse_basic", "_reconcile_twse_basic_cache"),
            ("TPEx", "tpex_issue", "tpex_issue", "_reconcile_tpex_issue_cache"),
        ]:
            good = self.reference(market)
            bad = copy.deepcopy(FIXTURES["twse"]["03737B"]) if market == "TWSE" else {
                **good, "Code": "78888B", "Name": "測試券商63熊01", "Type": "認售"}
            unknown = {**good, "權證代號" if market == "TWSE" else "Code": "08888Z"}
            payload = [good, bad, unknown]
            untouched = copy.deepcopy(payload)
            with tempfile.TemporaryDirectory() as temp, patch.object(v6, "CACHE_DIR", Path(temp)):
                with patch.object(v6, "_retry_official", return_value=(payload, {"ok": True})):
                    live, _ = v6.resilient_fetch_json(base.URLS[key])
                with patch.object(v6, "_retry_official", return_value=(None, {"ok": False})), \
                     patch.object(v6, reconcile, return_value=(True, {"covered": True})):
                    cached, meta = v6.resilient_fetch_json(base.URLS[key])
                self.assertTrue(meta["official_fallback"])
                self.assertEqual(len(cached), 3)  # Transport is raw; downstream filter must run.
                built_live, built_cache = self.build(market, live), self.build(market, cached)
                self.assertEqual(built_live, built_cache)
                self.assertEqual(payload, untouched)
                audit = built_cache["status"]["product_classification"]
                self.assertEqual((audit["plain_warrant_count"], audit["cbbc_excluded_count"],
                                  audit["unknown_excluded_count"]), (1, 1, 1))
                self.assertFalse(built_cache["status"]["mother_complete"])
                ranked = v5.rank_pure_warrants(built_cache["rows"])
                self.assertEqual(len(ranked), 1)

    def test_cbbc_does_not_consume_top100_slot(self):
        refs = []
        for n in range(101):
            refs.append({**self.reference("TWSE"), "權證代號": f"{31000+n:06}"})
        refs.extend(copy.deepcopy(FIXTURES["twse"][code]) for code in ("03737B", "03736B"))
        built = self.build("TWSE", refs)
        ranked = v5.rank_pure_warrants(built["rows"])
        self.assertEqual(len(ranked), 101)
        self.assertEqual(len(ranked[:100]), 100)
        self.assertFalse({"03737B", "03736B"} & {r["code"] for r in ranked})
        self.assertEqual(built["status"]["mother_count"], 101)
        self.assertTrue(built["status"]["mother_complete"])

    def test_unclassified_rows_cannot_enter_ranker_and_unknown_volume_unchanged(self):
        good = self.build("TWSE", [self.reference("TWSE")])["rows"][0]
        rows = [{**good, "volume_lots": None}, {**good, "product_classification": {}}, good]
        original = copy.deepcopy(rows)
        self.assertEqual(v5.rank_pure_warrants(rows), [good])
        self.assertEqual(rows, original)
        self.assertEqual(v5.MIN_REMAINING_DAYS_EXCLUSIVE, 180)
        self.assertEqual(v5.PRICE_MAX, 1.5)


if __name__ == "__main__":
    unittest.main()
