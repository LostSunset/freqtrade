from __future__ import annotations

import json
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

URLS = {
    "twse_basic": "https://openapi.twse.com.tw/v1/opendata/t187ap37_L",
    "twse_daily": "https://openapi.twse.com.tw/v1/opendata/t187ap42_L",
    "twse_underlying": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "twse_holiday": "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
    "tpex_issue": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_issue",
    "tpex_daily": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_daily_quts",
    "tpex_basic": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap37_O",
    "tpex_trade": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap42_O",
    "tpex_underlying": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
}

REQUEST_TIMEOUT = 45
MAX_WORKERS = len(URLS)


def fetch_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 tw-warrant-heartbeat/1.1",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ctx) as resp:
        raw = resp.read()
        text = raw.decode("utf-8-sig", errors="replace")
        return json.loads(text), len(raw), resp.headers.get("Content-Type")


def describe(data):
    result = {"type": type(data).__name__}
    if isinstance(data, list):
        result["rows"] = len(data)
        result["keys"] = sorted({k for row in data[:100] if isinstance(row, dict) for k in row.keys()})
        result["sample"] = data[0] if data else None
    elif isinstance(data, dict):
        result["top_keys"] = list(data.keys())[:100]
        lists = {}
        for key, value in data.items():
            if isinstance(value, list):
                lists[key] = {
                    "rows": len(value),
                    "keys": list(value[0].keys()) if value and isinstance(value[0], dict) else [],
                    "sample": value[0] if value else None,
                }
        if lists:
            result["list_fields"] = lists
    else:
        result["preview"] = str(data)[:500]
    return result


def probe_one(name: str, url: str):
    entry = {"url": url}
    started = datetime.now(timezone.utc)
    try:
        data, nbytes, ctype = fetch_json(url)
        entry.update({"ok": True, "bytes": nbytes, "content_type": ctype})
        entry.update(describe(data))
    except Exception as exc:
        entry.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    entry["elapsed_seconds"] = round(elapsed, 3)
    return name, entry


def main():
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_timeout_seconds": REQUEST_TIMEOUT,
        "endpoints": {},
    }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(probe_one, name, url) for name, url in URLS.items()]
        for future in as_completed(futures):
            name, entry = future.result()
            out["endpoints"][name] = entry
            print(f"{name}: {'OK' if entry.get('ok') else 'FAIL'} ({entry.get('elapsed_seconds')}s)")

    out["endpoints"] = {name: out["endpoints"][name] for name in URLS}
    target = Path(__file__).with_name("latest_probe.json")
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
