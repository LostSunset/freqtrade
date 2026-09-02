from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URLS = {
    "tpex_warrant_monthly_quts": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_monthly_quts",
    "tpex_warrant_daily_quts": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_daily_quts",
}


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 tw-warrant-history-probe/1.2", "Accept-Encoding": "identity"})
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
        out["nonblank_close_count"] = sum(1 for row in data if isinstance(row, dict) and str(row.get("Close", "")).strip() not in {"", "-", "--"})
        out["nonzero_trade_count"] = sum(1 for row in data if isinstance(row, dict) and str(row.get("TradeVol.", "")).replace(",", "").strip() not in {"", "0", "0.0"})
    return out


def parse_twse_closes(payload):
    tables = payload.get("tables", []) if isinstance(payload, dict) else []
    best = None
    for table in tables:
        fields = table.get("fields", []) if isinstance(table, dict) else []
        data = table.get("data", []) if isinstance(table, dict) else []
        if "證券代號" in fields and "收盤價" in fields:
            if best is None or len(data) > len(best.get("data", [])):
                best = table
    out = {}
    if not best:
        return out
    fields = best["fields"]
    ci, pi = fields.index("證券代號"), fields.index("收盤價")
    ni = fields.index("證券名稱") if "證券名稱" in fields else None
    for row in best.get("data", []):
        if not isinstance(row, list) or max(ci, pi) >= len(row):
            continue
        code = str(row[ci]).strip()
        close = str(row[pi]).strip()
        out[code] = {"close": close, "name": str(row[ni]).strip() if ni is not None and ni < len(row) else ""}
    return out


def main():
    result = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "endpoints": {}}
    payloads = {}
    for name, url in URLS.items():
        try:
            data, nbytes = fetch(url)
            payloads[name] = data
            result["endpoints"][name] = {"ok": True, "url": url, "bytes": nbytes, **describe(data)}
        except Exception as exc:
            result["endpoints"][name] = {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}

    monthly = payloads.get("tpex_warrant_monthly_quts") or []
    daily = payloads.get("tpex_warrant_daily_quts") or []
    m = {str(r.get("Code", "")).strip(): r for r in monthly if isinstance(r, dict)}
    d = {str(r.get("Code", "")).strip(): r for r in daily if isinstance(r, dict)}
    carry = []
    for code, mr in m.items():
        dr = d.get(code, {})
        mc = str(mr.get("Close", "")).strip()
        dc = str(dr.get("Close", "")).strip()
        if mc not in {"", "-", "--"} and dc in {"", "-", "--"}:
            carry.append({"Code": code, "Name": mr.get("Name"), "monthly_Close": mc, "daily_Close": dc})
    result["tpex_comparison"] = {
        "monthly_nonblank_but_daily_blank_count": len(carry),
        "samples": carry[:20],
    }

    twse_base = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    twse_payloads = {}
    for ymd in ["20260901", "20260831", "20260828"]:
        url = twse_base + "?" + urllib.parse.urlencode({"date": ymd, "type": "ALL", "response": "json"})
        try:
            data, nbytes = fetch(url)
            twse_payloads[ymd] = data
            result["endpoints"][f"twse_mi_{ymd}"] = {"ok": True, "url": url, "bytes": nbytes}
        except Exception as exc:
            result["endpoints"][f"twse_mi_{ymd}"] = {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}

    c1 = parse_twse_closes(twse_payloads.get("20260901", {}))
    c2 = parse_twse_closes(twse_payloads.get("20260831", {}))
    c3 = parse_twse_closes(twse_payloads.get("20260828", {}))
    day1_blank = {code for code, r in c1.items() if r["close"] in {"", "-", "--"}}
    carry1 = [code for code in day1_blank if code in c2 and c2[code]["close"] not in {"", "-", "--"}]
    still_blank = [code for code in day1_blank if code not in c2 or c2[code]["close"] in {"", "-", "--"}]
    carry2 = [code for code in still_blank if code in c3 and c3[code]["close"] not in {"", "-", "--"}]
    result["twse_comparison"] = {
        "sep01_security_count": len(c1),
        "sep01_blank_close_count": len(day1_blank),
        "recovered_from_aug31_count": len(carry1),
        "additional_recovered_from_aug28_count": len(carry2),
        "sample_aug31": [{"Code": code, "Name": c2[code]["name"], "Close": c2[code]["close"]} for code in carry1[:20]],
    }

    path = Path(__file__).with_name("history_probe.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
