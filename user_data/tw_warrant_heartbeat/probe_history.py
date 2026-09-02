from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URLS = {
    "tpex_warrant_monthly_quts": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_monthly_quts",
    "tpex_warrant_daily_quts": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_daily_quts",
}


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 tw-warrant-history-probe/1.0", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8-sig")), len(raw)


def describe(data):
    out = {"type": type(data).__name__}
    if isinstance(data, list):
        out["rows"] = len(data)
        out["keys"] = sorted({k for row in data[:200] if isinstance(row, dict) for k in row})
        out["samples"] = data[:3]
        dates = sorted({str(row.get("Date")) for row in data if isinstance(row, dict) and row.get("Date")})
        out["date_count"] = len(dates)
        out["first_dates"] = dates[:10]
        out["last_dates"] = dates[-10:]
    return out


def main():
    result = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "endpoints": {}}
    for name, url in URLS.items():
        try:
            data, nbytes = fetch(url)
            result["endpoints"][name] = {"ok": True, "url": url, "bytes": nbytes, **describe(data)}
        except Exception as exc:
            result["endpoints"][name] = {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
    path = Path(__file__).with_name("history_probe.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
