"""Replay a SHA256-verified official snapshot without any network or repo output writes.

Usage: python replay_official_inputs.py --code-dir DIR --inputs-dir DIR
       --output-dir DIR [--mode live|cache]
The input manifest is produced by the task's one-time public-source capture script.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-dir", type=Path, required=True)
    parser.add_argument("--inputs-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("live", "cache"), default="live")
    args = parser.parse_args()
    if args.output_dir.resolve() == args.inputs_dir.resolve():
        parser.error("Output directory must differ from immutable inputs")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.inputs_dir / "manifest.json").read_text(encoding="utf-8"))
    frozen_payloads = {}
    for key, meta in manifest["sources"].items():
        if not meta.get("ok"):
            raise RuntimeError(f"Incomplete input capture: {key}")
        raw = gzip.decompress((args.inputs_dir / meta["file"]).read_bytes())
        if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise RuntimeError(f"Immutable input hash mismatch: {key}")
        frozen_payloads[key] = json.loads(raw)

    def no_network(*_args, **_kwargs):
        raise AssertionError("Network is forbidden during immutable-input replay")

    urllib.request.urlopen = no_network
    socket.socket.connect = no_network
    sys.path.insert(0, str(args.code_dir.resolve()))
    import build_heartbeat_v6 as v6

    v5, base = v6.v5, v6.base
    execution_date = manifest["execution_date"]

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(execution_date + "T08:31:38+08:00").astimezone(tz)

    v5.datetime = v6.datetime = FrozenDateTime
    base.LATEST_PATH = args.output_dir / "latest.json"
    base.PREVIOUS_PATH = args.output_dir / "previous.json"
    v6.OUTER_RETRIES = 1
    v6._state = {"holidays": None, "mi": {}, "tpex_daily": None}

    def replay_fetch(url, params=None):
        key = next((k for k, value in base.URLS.items() if value == url), None)
        if key is None:
            raise AssertionError(f"Uncaptured source requested: {url}")
        if params and params.get("date") != manifest["close_date"].replace("-", ""):
            raise AssertionError(f"Uncaptured date requested: {params}")
        if args.mode == "cache" and key in {"twse_basic", "tpex_issue"}:
            return None, {"ok": False, "error": "simulated reference endpoint outage", "url": url}
        return copy.deepcopy(frozen_payloads[key]), {"ok": True, "url": url, "offline_replay": True}

    def replay_history(day):
        key = f"history_{day.isoformat()}"
        mi = base.parse_twse_mi(copy.deepcopy(frozen_payloads[key]))
        usable = bool(mi.get("security_by_code"))
        return day, mi, {"ok": usable, "offline_replay": True}, usable

    v6._original_fetch_json = replay_fetch
    v5.v4.fetch_twse_history = replay_history
    with tempfile.TemporaryDirectory(prefix="warrant-replay-cache-") as cache:
        v6.CACHE_DIR = Path(cache)
        if args.mode == "cache":
            for key in ("twse_basic", "tpex_issue"):
                v6._save_cache(key, frozen_payloads[key], base.URLS[key])
        v5.main()

    # Re-read source bytes after the full pipeline to prove the inputs stayed intact.
    for key, meta in manifest["sources"].items():
        raw = gzip.decompress((args.inputs_dir / meta["file"]).read_bytes())
        if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise RuntimeError(f"Input changed during replay: {key}")
    result = json.loads(base.LATEST_PATH.read_text(encoding="utf-8"))
    summary = {
        "base_sha": manifest["base_sha"], "mode": args.mode, "network_disabled": True,
        "verified_input_count": len(frozen_payloads), "execution_date": execution_date,
        "filter_funnel": result["filter_funnel"],
        "unknown_volume": {m: result["data_status"][m]["volume_unknown_count"]
                           for m in ("twse", "tpex")},
        "top100_codes": [r["warrant_code"] for r in result["pure_warrant_top100"]],
        "final_codes": [r["warrant_code"] for r in result["formal_candidates_all"]],
        "classification_excluded": {
            m: result["data_status"][m].get("product_classification", {}).get("excluded", [])
            for m in ("twse", "tpex")
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
