from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import build_heartbeat_v3 as v3

base = v3.base
_original_build_twse = base.build_twse
_original_build_tpex = base.build_tpex
_original_output_row = base.output_row

HISTORY_LOOKBACK_TRADING_DAYS = 10
HISTORY_FETCH_WORKERS = 5
HISTORY_REQUEST_TIMEOUT = 45


def previous_weekdays(d: date, count: int) -> list[date]:
    out: list[date] = []
    cur = d - timedelta(days=1)
    while len(out) < count:
        if cur.weekday() < 5:
            out.append(cur)
        cur -= timedelta(days=1)
    return out


def fetch_twse_history(d: date):
    params = {"date": d.strftime("%Y%m%d"), "type": "ALL", "response": "json"}
    url = f"{base.URLS['twse_mi_index']}?{urllib.parse.urlencode(params)}"
    started = time.monotonic()
    meta = {"url": url, "ok": False}
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 tw-warrant-heartbeat-history/4.1",
                "Accept": "application/json,text/plain,*/*",
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(
            req,
            timeout=HISTORY_REQUEST_TIMEOUT,
            context=ssl.create_default_context(),
        ) as resp:
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8-sig", errors="strict"))
        mi = base.parse_twse_mi(payload)
        usable = bool(mi.get("security_by_code"))
        meta.update({
            "ok": usable,
            "bytes": len(raw),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        return d, mi, meta, usable
    except Exception as exc:
        meta.update({
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        return d, {"security_by_code": {}, "security_by_name": {}, "index_by_name": {}}, meta, False


def build_twse_v4(execution_date, close_date, basic, daily, stock_rows, mi, status):
    result = _original_build_twse(execution_date, close_date, basic, daily, stock_rows, mi, status)

    for row in result["rows"]:
        if row.get("warrant_close") is not None and row.get("underlying_close") is not None:
            row["price_snapshot_date"] = close_date.isoformat()
            row["price_snapshot_mode"] = "最近完整交易日同步收盤"

    unresolved = [
        row for row in result["rows"]
        if "權證收盤價" in row.get("hard_missing", [])
    ]
    if not unresolved:
        status["historical_carry_forward"] = {
            "lookback_weekdays": HISTORY_LOOKBACK_TRADING_DAYS,
            "request_timeout_seconds": HISTORY_REQUEST_TIMEOUT,
            "recovered": 0,
            "remaining_unresolved": 0,
        }
        return result

    history_dates = previous_weekdays(close_date, HISTORY_LOOKBACK_TRADING_DAYS)
    history: list[tuple[date, dict, dict]] = []
    history_failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=HISTORY_FETCH_WORKERS) as pool:
        futures = [pool.submit(fetch_twse_history, d) for d in history_dates]
        for future in as_completed(futures):
            d, hist_mi, meta, usable = future.result()
            if usable:
                history.append((d, hist_mi, meta))
            else:
                history_failures.append({"date": d.isoformat(), **meta})
    history.sort(key=lambda x: x[0], reverse=True)

    recovered = 0
    recovered_by_date: dict[str, int] = {}
    for row in unresolved:
        code = row.get("code")
        underlying = row.get("underlying")
        for d, hist_mi, _meta in history:
            warrant_item = hist_mi["security_by_code"].get(code, {})
            warrant_close = warrant_item.get("close_num")
            if warrant_close is None:
                continue

            underlying_close = base.twse_underlying_close(underlying, hist_mi, [])
            if underlying_close is None:
                continue

            row["warrant_close"] = warrant_close
            row["underlying_close"] = underlying_close
            row["price_snapshot_date"] = d.isoformat()
            row["price_snapshot_mode"] = "官方歷史同步收盤 carry-forward"
            row.pop("premium_pct", None)
            row.pop("directional_moneyness_pct", None)
            row.pop("remaining_days", None)
            missing, failed = base.hard_status(row, execution_date)
            row["hard_missing"] = missing
            row["hard_failed"] = failed
            row.setdefault("source", []).append(
                f"TWSE MI_INDEX 同步歷史收盤 carry-forward ({d.isoformat()})"
            )
            recovered += 1
            recovered_by_date[d.isoformat()] = recovered_by_date.get(d.isoformat(), 0) + 1
            break

    complete = sum(1 for row in result["rows"] if not row.get("hard_missing"))
    status["hard_fields_complete_count"] = complete
    status["hard_fields_complete"] = bool(status.get("mother_count") and complete == status["mother_count"])
    status["historical_carry_forward"] = {
        "rule": "僅在權證收盤價與標的收盤價可由同一TWSE MI_INDEX歷史交易日同步取得時回補",
        "lookback_weekdays": HISTORY_LOOKBACK_TRADING_DAYS,
        "request_timeout_seconds": HISTORY_REQUEST_TIMEOUT,
        "history_dates_used": [d.isoformat() for d, _, _ in history],
        "history_fetch_failures": history_failures,
        "initial_warrant_close_missing": len(unresolved),
        "recovered": recovered,
        "recovered_by_date": recovered_by_date,
        "remaining_unresolved": sum(1 for row in result["rows"] if "權證收盤價" in row.get("hard_missing", [])),
    }
    return result


def build_tpex_v4(execution_date, close_date, issue, daily, trade, underlying_rows, status):
    result = _original_build_tpex(execution_date, close_date, issue, daily, trade, underlying_rows, status)
    for row in result["rows"]:
        if not row.get("hard_missing") and row.get("warrant_close") is not None and row.get("underlying_close") is not None:
            row["price_snapshot_date"] = close_date.isoformat()
            row["price_snapshot_mode"] = "最近完整交易日同步收盤"
    status["historical_carry_forward"] = {
        "monthly_endpoint": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_monthly_quts",
        "used_for_hard_certification": False,
        "reason": "月報Date只有年月，無法證明權證Close與標的Close為同一實際交易日；不做非同步溢價計算",
    }
    return result


def output_row_v4(row):
    out = _original_output_row(row)
    out["price_snapshot_date"] = row.get("price_snapshot_date") or "未知"
    out["price_snapshot_mode"] = row.get("price_snapshot_mode") or "未知"
    return out


base.build_twse = build_twse_v4
base.build_tpex = build_tpex_v4
base.output_row = output_row_v4

if __name__ == "__main__":
    base.main()
