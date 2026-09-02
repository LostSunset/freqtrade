from __future__ import annotations

import html
import http.client
import json
import math
import re
import ssl
import time
import urllib.parse
import urllib.request
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
BASE_DIR = Path(__file__).resolve().parent
LATEST_PATH = BASE_DIR / "latest.json"
PREVIOUS_PATH = BASE_DIR / "previous.json"

URLS = {
    "twse_basic": "https://openapi.twse.com.tw/v1/opendata/t187ap37_L",
    "twse_daily": "https://openapi.twse.com.tw/v1/opendata/t187ap42_L",
    "twse_underlying": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "twse_holiday": "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
    "twse_mi_index": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
    "tpex_issue": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_issue",
    "tpex_daily": "https://www.tpex.org.tw/openapi/v1/tpex_warrant_daily_quts",
    "tpex_trade": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap42_O",
    "tpex_underlying": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
    "warrant_platform": "https://warrants.twse.com.tw/",
}

REQUEST_TIMEOUT = 180
RETRIES = 3


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]*>", "", text)
    return text.replace("\u3000", " ").strip()


def norm_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value)).upper()
    text = re.sub(r"\s+", "", text)
    return text


def to_float(value: Any) -> float | None:
    text = clean_text(value).replace(",", "").replace("％", "").replace("%", "")
    if text in {"", "--", "---", "N/A", "NA", "-", "除權", "除息"}:
        return None
    text = text.replace("＋", "+").replace("－", "-")
    text = re.sub(r"[^0-9eE+\-.]", "", text)
    if text in {"", "+", "-", "."}:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return None if number is None else int(round(number))


def roc_or_gregorian_date(value: Any) -> date | None:
    text = re.sub(r"\D", "", clean_text(value))
    if not text:
        return None
    try:
        if len(text) == 8 and int(text[:4]) >= 1900:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        if len(text) == 7:
            return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7]))
    except ValueError:
        return None
    return None


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def fetch_json(url: str, params: dict[str, str] | None = None) -> tuple[Any, dict[str, Any]]:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {
        "User-Agent": "Mozilla/5.0 tw-warrant-heartbeat/2.0",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Encoding": "identity",
    }
    last_error: Exception | None = None
    started = time.monotonic()
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ssl.create_default_context()) as resp:
                raw = resp.read()
                data = json.loads(raw.decode("utf-8-sig", errors="strict"))
                return data, {
                    "ok": True,
                    "url": url,
                    "bytes": len(raw),
                    "attempt": attempt,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
        except (http.client.IncompleteRead, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < RETRIES:
                time.sleep(1.5 * attempt)
    return None, {
        "ok": False,
        "url": url,
        "attempt": RETRIES,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "error": f"{type(last_error).__name__}: {last_error}",
    }


def holiday_set(rows: Any) -> set[date]:
    out: set[date] = set()
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                d = roc_or_gregorian_date(row.get("Date"))
                if d:
                    out.add(d)
    return out


def is_business_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def last_completed_trading_day(execution_date: date, holidays: set[date]) -> date:
    d = execution_date - timedelta(days=1)
    for _ in range(14):
        if is_business_day(d, holidays):
            return d
        d -= timedelta(days=1)
    raise RuntimeError("Unable to resolve previous trading day")


def market_state(execution_date: date, holidays: set[date]) -> str:
    if execution_date.weekday() >= 5:
        return "週末"
    if execution_date in holidays:
        return "休市"
    return "交易日盤前"


def find_index(rows: list[str], *needles: str) -> int | None:
    normalized = [norm_name(x) for x in rows]
    for needle in needles:
        n = norm_name(needle)
        for i, field in enumerate(normalized):
            if field == n:
                return i
        for i, field in enumerate(normalized):
            if n and n in field:
                return i
    return None


def parse_twse_mi(payload: Any) -> dict[str, Any]:
    result = {
        "security_by_code": {},
        "security_by_name": {},
        "index_by_name": {},
        "selected_security_table": None,
        "table_summaries": [],
    }
    if not isinstance(payload, dict):
        return result

    tables = payload.get("tables")
    if not isinstance(tables, list):
        tables = []
        # Backward-compatible TWSE response shapes.
        for key, value in payload.items():
            if key.startswith("data") and isinstance(value, list):
                suffix = key[4:]
                fields = payload.get(f"fields{suffix}") or []
                tables.append({"title": key, "fields": fields, "data": value})

    security_candidates: list[tuple[int, dict[str, Any]]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        fields = [clean_text(x) for x in (table.get("fields") or [])]
        data = table.get("data") or []
        title = clean_text(table.get("title"))
        result["table_summaries"].append({"title": title, "rows": len(data), "fields": fields})
        code_i = find_index(fields, "證券代號")
        close_i = find_index(fields, "收盤價")
        if code_i is not None and close_i is not None and isinstance(data, list):
            security_candidates.append((len(data), table))

        # Index table support.
        idx_name_i = find_index(fields, "指數名稱", "指數")
        idx_close_i = find_index(fields, "收盤指數")
        if idx_name_i is not None and idx_close_i is not None and isinstance(data, list):
            for row in data:
                if not isinstance(row, list) or max(idx_name_i, idx_close_i) >= len(row):
                    continue
                name = norm_name(row[idx_name_i])
                close = to_float(row[idx_close_i])
                if name and close is not None:
                    result["index_by_name"][name] = close

    if not security_candidates:
        return result
    _, table = max(security_candidates, key=lambda x: x[0])
    fields = [clean_text(x) for x in (table.get("fields") or [])]
    rows = table.get("data") or []
    result["selected_security_table"] = {
        "title": clean_text(table.get("title")),
        "rows": len(rows),
        "fields": fields,
    }

    indexes = {
        "code": find_index(fields, "證券代號"),
        "name": find_index(fields, "證券名稱"),
        "close": find_index(fields, "收盤價"),
        "volume": find_index(fields, "成交股數", "成交數量"),
        "value": find_index(fields, "成交金額"),
        "bid": find_index(fields, "最後揭示買價"),
        "ask": find_index(fields, "最後揭示賣價"),
        "bid_qty": find_index(fields, "最後揭示買量"),
        "ask_qty": find_index(fields, "最後揭示賣量"),
    }
    for row in rows:
        if not isinstance(row, list):
            continue
        ci = indexes["code"]
        if ci is None or ci >= len(row):
            continue
        code = clean_text(row[ci])
        if not code:
            continue
        item: dict[str, Any] = {"code": code}
        for key, idx in indexes.items():
            if idx is not None and idx < len(row):
                item[key] = clean_text(row[idx])
        item["close_num"] = to_float(item.get("close"))
        item["volume_num"] = to_int(item.get("volume"))
        item["value_num"] = to_int(item.get("value"))
        result["security_by_code"][code] = item
        name = norm_name(item.get("name"))
        if name:
            result["security_by_name"][name] = item
    return result


def twse_underlying_close(name_or_code: Any, mi: dict[str, Any], stock_rows: Any) -> float | None:
    key = clean_text(name_or_code)
    if not key:
        return None
    by_code = mi["security_by_code"].get(key)
    if by_code and by_code.get("close_num") is not None:
        return by_code["close_num"]
    n = norm_name(key)
    by_name = mi["security_by_name"].get(n)
    if by_name and by_name.get("close_num") is not None:
        return by_name["close_num"]
    if n in mi["index_by_name"]:
        return mi["index_by_name"][n]
    if isinstance(stock_rows, list):
        for row in stock_rows:
            if not isinstance(row, dict):
                continue
            if clean_text(row.get("Code")) == key or norm_name(row.get("Name")) == n:
                return to_float(row.get("ClosingPrice"))
    return None


def tpex_underlying_map(rows: Any) -> tuple[dict[str, float], dict[str, float]]:
    by_code: dict[str, float] = {}
    by_name: dict[str, float] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            close = to_float(row.get("Close"))
            if close is None:
                continue
            code = clean_text(row.get("SecuritiesCompanyCode"))
            name = norm_name(row.get("CompanyName"))
            if code:
                by_code[code] = close
            if name:
                by_name[name] = close
    return by_code, by_name


def premium(kind: str, strike: float, warrant_close: float, ratio: float, underlying_close: float) -> float:
    if kind == "認購":
        return ((strike + warrant_close / ratio) / underlying_close - 1.0) * 100.0
    return (1.0 - (strike - warrant_close / ratio) / underlying_close) * 100.0


def directional_moneyness(kind: str, strike: float, underlying_close: float) -> float:
    if kind == "認購":
        return (underlying_close - strike) / strike * 100.0
    return (strike - underlying_close) / strike * 100.0


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def quality_score(row: dict[str, Any]) -> float:
    # Internal ranking score, deliberately distinct from the user's liquidity score.
    score = 50.0  # all three hard conditions already passed
    m = row.get("directional_moneyness_pct")
    if m is not None:
        if -8 <= m <= 5:
            score += 20
        else:
            distance = (-8 - m) if m < -8 else (m - 5)
            score += max(0.0, 20.0 - distance * 2.0)
    vol = row.get("volume_lots")
    if vol is not None:
        score += min(10.0, max(0.0, 10.0 * vol / 300.0))
    amount = row.get("trade_value_ntd")
    if amount is not None:
        score += min(10.0, max(0.0, 10.0 * amount / 300000.0))
    p = row.get("premium_pct")
    if p is not None:
        score += max(0.0, min(10.0, 10.0 - max(p, 0.0)))
    return round(score, 2)


def soft_summary(row: dict[str, Any]) -> str:
    flags: list[str] = []
    m = row.get("directional_moneyness_pct")
    flags.append("價內外通過" if m is not None and -8 <= m <= 5 else "價內外未通過")
    v = row.get("volume_lots")
    flags.append("量通過" if v is not None and v >= 300 else ("量未知" if v is None else "量未通過"))
    a = row.get("trade_value_ntd")
    flags.append("額通過" if a is not None and a >= 300000 else ("額未知" if a is None else "額未通過"))
    flags.extend(["價差未知", "流動性分數未知", "有效槓桿未知"])
    return "；".join(flags)


def hard_status(row: dict[str, Any], execution_date: date) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    failed: list[str] = []
    expiry = row.get("expiry_date")
    strike = row.get("strike")
    ratio = row.get("exercise_ratio")
    wc = row.get("warrant_close")
    uc = row.get("underlying_close")
    if expiry is None:
        missing.append("到期日")
    if strike is None:
        missing.append("履約價")
    if ratio is None or ratio <= 0:
        missing.append("行使比例")
    if wc is None:
        missing.append("權證收盤價")
    if uc is None:
        missing.append("標的收盤價")
    if missing:
        return missing, failed

    days = (expiry - execution_date).days
    row["remaining_days"] = days
    row["premium_pct"] = premium(row["type"], strike, wc, ratio, uc)
    row["directional_moneyness_pct"] = directional_moneyness(row["type"], strike, uc)
    if not (365 <= days <= 3650):
        failed.append("剩餘天數不在365～3650")
    if row["premium_pct"] > 10:
        failed.append("溢價率>10%")
    if not (0.1 <= wc <= 1.5):
        failed.append("權證收盤價不在0.1～1.5")
    return missing, failed


def output_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "warrant_code": row.get("code"),
        "name": row.get("name"),
        "underlying": row.get("underlying"),
        "issuer": row.get("issuer") or "未知",
        "warrant_close": round_or_none(row.get("warrant_close"), 4),
        "price_nature": "最近官方最後收盤價",
        "remaining_days": row.get("remaining_days"),
        "premium_pct": round_or_none(row.get("premium_pct"), 4),
        "directional_moneyness_pct": round_or_none(row.get("directional_moneyness_pct"), 4),
        "effective_leverage": "未知",
        "volume_lots": row.get("volume_lots"),
        "trade_value_ntd": row.get("trade_value_ntd"),
        "spread_pct": "未知",
        "liquidity_score": "未知",
        "suspected_broker_involvement": "無法判斷",
        "quality_score_internal": row.get("quality_score_internal"),
        "quality_summary": row.get("quality_summary"),
        "source": row.get("source"),
    }


def build_twse(execution_date: date, close_date: date, basic: Any, daily: Any, stock_rows: Any, mi: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    daily_by_code: dict[str, dict[str, Any]] = {}
    daily_dates: set[date] = set()
    if isinstance(daily, list):
        for row in daily:
            if not isinstance(row, dict):
                continue
            code = clean_text(row.get("權證代號"))
            d = roc_or_gregorian_date(row.get("交易日期"))
            if d:
                daily_dates.add(d)
            if code:
                daily_by_code[code] = row

    all_rows: list[dict[str, Any]] = []
    mother = 0
    basic_dates: set[date] = set()
    if isinstance(basic, list):
        for b in basic:
            if not isinstance(b, dict):
                continue
            kind = clean_text(b.get("權證類型"))
            if kind not in {"認購", "認售"}:
                continue
            expiry = roc_or_gregorian_date(b.get("履約截止日"))
            listed_or_active = expiry is not None and expiry >= execution_date
            if not listed_or_active:
                continue
            mother += 1
            report_d = roc_or_gregorian_date(b.get("出表日期"))
            if report_d:
                basic_dates.add(report_d)
            code = clean_text(b.get("權證代號"))
            market = mi["security_by_code"].get(code, {})
            daily_row = daily_by_code.get(code, {})
            wc = market.get("close_num")
            strike = to_float(b.get("最新履約價格(元)/履約指數"))
            per_thousand = to_float(b.get("最新標的履約配發數量(每仟單位權證)"))
            ratio = None if per_thousand is None else per_thousand / 1000.0
            underlying = clean_text(b.get("標的證券/指數"))
            uc = twse_underlying_close(underlying, mi, stock_rows)

            trade_value = to_int(daily_row.get("成交金額"))
            volume_lots = to_int(daily_row.get("成交張數"))
            # If t187ap42 is stale or missing for this code, use the official MI_INDEX row.
            daily_d = roc_or_gregorian_date(daily_row.get("交易日期"))
            if daily_d != close_date:
                trade_value = market.get("value_num")
                units = market.get("volume_num")
                volume_lots = None if units is None else int(units // 1000)

            row = {
                "market": "TWSE",
                "code": code,
                "name": clean_text(b.get("權證簡稱")),
                "underlying": underlying,
                "issuer": None,
                "type": kind,
                "expiry_date": expiry,
                "strike": strike,
                "exercise_ratio": ratio,
                "warrant_close": wc,
                "underlying_close": uc,
                "volume_lots": volume_lots,
                "trade_value_ntd": trade_value,
                "source": [
                    f"TWSE t187ap37_L ({iso(report_d) or '日期未知'})",
                    f"TWSE MI_INDEX type=ALL ({iso(close_date)})",
                    f"TWSE t187ap42_L ({iso(daily_d) or '日期未知'})",
                ],
            }
            missing, failed = hard_status(row, execution_date)
            row["hard_missing"] = missing
            row["hard_failed"] = failed
            if not missing:
                row["premium_pct"] = round(row["premium_pct"], 8)
                row["directional_moneyness_pct"] = round(row["directional_moneyness_pct"], 8)
            all_rows.append(row)

    complete_hard = sum(1 for r in all_rows if not r["hard_missing"])
    status.update({
        "mother_count": mother,
        "mother_complete": isinstance(basic, list) and mother > 0,
        "basic_data_dates": sorted(iso(d) for d in basic_dates),
        "daily_data_dates": sorted(iso(d) for d in daily_dates),
        "hard_fields_complete_count": complete_hard,
        "hard_fields_complete": mother > 0 and complete_hard == mother,
        "mi_security_rows": len(mi["security_by_code"]),
        "mi_selected_table": mi.get("selected_security_table"),
    })
    return {"rows": all_rows, "status": status}


def build_tpex(execution_date: date, close_date: date, issue: Any, daily: Any, trade: Any, underlying_rows: Any, status: dict[str, Any]) -> dict[str, Any]:
    daily_by_code: dict[str, dict[str, Any]] = {}
    daily_dates: set[date] = set()
    if isinstance(daily, list):
        for row in daily:
            if not isinstance(row, dict):
                continue
            code = clean_text(row.get("Code"))
            d = roc_or_gregorian_date(row.get("Date"))
            if d:
                daily_dates.add(d)
            if code:
                daily_by_code[code] = row

    trade_by_code: dict[str, dict[str, Any]] = {}
    trade_dates: set[date] = set()
    if isinstance(trade, list):
        for row in trade:
            if not isinstance(row, dict):
                continue
            code = clean_text(row.get("權證代號"))
            d = roc_or_gregorian_date(row.get("交易日期") or row.get("Date"))
            if d:
                trade_dates.add(d)
            if code:
                trade_by_code[code] = row

    u_by_code, u_by_name = tpex_underlying_map(underlying_rows)
    issue_dates: set[date] = set()
    mother = 0
    all_rows: list[dict[str, Any]] = []
    if isinstance(issue, list):
        for b in issue:
            if not isinstance(b, dict):
                continue
            kind = clean_text(b.get("Type"))
            if kind not in {"認購", "認售"}:
                continue
            expiry = roc_or_gregorian_date(b.get("ExpiryDate"))
            listed = roc_or_gregorian_date(b.get("ListedDate"))
            if expiry is None or expiry < execution_date:
                continue
            if listed and listed > close_date:
                continue
            mother += 1
            issue_d = roc_or_gregorian_date(b.get("Date"))
            if issue_d:
                issue_dates.add(issue_d)
            code = clean_text(b.get("Code"))
            q = daily_by_code.get(code, {})
            t = trade_by_code.get(code, {})
            q_date = roc_or_gregorian_date(q.get("Date"))
            t_date = roc_or_gregorian_date(t.get("交易日期") or t.get("Date"))
            wc = to_float(q.get("Close"))
            uc = to_float(q.get("UnderlyingStockClosePrice"))
            under_code = clean_text(b.get("UnderlyingStockCode"))
            under_name = clean_text(b.get("UnderlyingStock"))
            if uc is None:
                uc = u_by_code.get(under_code) or u_by_name.get(norm_name(under_name))

            volume_units = to_int(q.get("TradeVol."))
            trade_value = to_int(q.get("TradeValue"))
            if t_date == close_date:
                t_units = to_int(t.get("成交數量"))
                t_value = to_int(t.get("成交金額"))
                if t_units is not None:
                    volume_units = t_units
                if t_value is not None:
                    trade_value = t_value
            volume_lots = None if volume_units is None else int(volume_units // 1000)

            row = {
                "market": "TPEx",
                "code": code,
                "name": clean_text(b.get("Name")),
                "underlying": under_name or under_code,
                "issuer": None,
                "type": kind,
                "expiry_date": expiry,
                "strike": to_float(b.get("LatestExercisePrice")),
                "exercise_ratio": to_float(b.get("Latest ExerciseRatio")),
                "warrant_close": wc,
                "underlying_close": uc,
                "volume_lots": volume_lots,
                "trade_value_ntd": trade_value,
                "source": [
                    f"TPEx tpex_warrant_issue ({iso(issue_d) or '日期未知'})",
                    f"TPEx tpex_warrant_daily_quts ({iso(q_date) or '日期未知'})",
                    f"TPEx mopsfin_t187ap42_O ({iso(t_date) or '日期未知'})",
                ],
            }
            missing, failed = hard_status(row, execution_date)
            row["hard_missing"] = missing
            row["hard_failed"] = failed
            if not missing:
                row["premium_pct"] = round(row["premium_pct"], 8)
                row["directional_moneyness_pct"] = round(row["directional_moneyness_pct"], 8)
            all_rows.append(row)

    complete_hard = sum(1 for r in all_rows if not r["hard_missing"])
    status.update({
        "mother_count": mother,
        "mother_complete": isinstance(issue, list) and mother > 0,
        "issue_data_dates": sorted(iso(d) for d in issue_dates),
        "daily_data_dates": sorted(iso(d) for d in daily_dates),
        "trade_data_dates": sorted(iso(d) for d in trade_dates),
        "hard_fields_complete_count": complete_hard,
        "hard_fields_complete": mother > 0 and complete_hard == mother,
    })
    return {"rows": all_rows, "status": status}


def nearest(rows: list[dict[str, Any]], kind: str, n: int = 3) -> list[dict[str, Any]]:
    candidates = [r for r in rows if r["type"] == kind and not r["hard_missing"] and r["hard_failed"]]
    def penalty(r: dict[str, Any]) -> tuple:
        p = 0.0
        days = r.get("remaining_days")
        if days is not None:
            if days < 365:
                p += (365 - days) / 30
            elif days > 3650:
                p += (days - 3650) / 30
        prem = r.get("premium_pct")
        if prem is not None and prem > 10:
            p += prem - 10
        wc = r.get("warrant_close")
        if wc is not None:
            if wc < 0.1:
                p += (0.1 - wc) * 20
            elif wc > 1.5:
                p += (wc - 1.5) * 2
        return (len(r["hard_failed"]), p)
    out = []
    for r in sorted(candidates, key=penalty)[:n]:
        x = output_row(r)
        x["hard_fail_reasons"] = r["hard_failed"]
        out.append(x)
    return out


def summarize_candidates(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    passed = [r for r in rows if r["type"] == kind and not r["hard_missing"] and not r["hard_failed"]]
    for r in passed:
        r["quality_score_internal"] = quality_score(r)
        r["quality_summary"] = soft_summary(r)
    passed.sort(
        key=lambda r: (
            -(r.get("quality_score_internal") or 0),
            -(r.get("trade_value_ntd") or 0),
            r.get("premium_pct") if r.get("premium_pct") is not None else 999,
        )
    )
    return [output_row(r) for r in passed[:25]]


def endpoint_entry(meta: dict[str, Any], data: Any) -> dict[str, Any]:
    out = dict(meta)
    if isinstance(data, list):
        out["rows"] = len(data)
    elif isinstance(data, dict):
        out["top_keys"] = list(data.keys())[:20]
    return out


def main() -> None:
    now = datetime.now(TZ)
    execution_date = now.date()

    holidays_raw, holidays_meta = fetch_json(URLS["twse_holiday"])
    holidays = holiday_set(holidays_raw)
    close_date = last_completed_trading_day(execution_date, holidays)

    twse_basic, twse_basic_meta = fetch_json(URLS["twse_basic"])
    twse_daily, twse_daily_meta = fetch_json(URLS["twse_daily"])
    twse_underlying, twse_underlying_meta = fetch_json(URLS["twse_underlying"])
    twse_mi_raw, twse_mi_meta = fetch_json(
        URLS["twse_mi_index"],
        {"date": close_date.strftime("%Y%m%d"), "type": "ALL", "response": "json"},
    )
    mi = parse_twse_mi(twse_mi_raw)

    tpex_issue, tpex_issue_meta = fetch_json(URLS["tpex_issue"])
    tpex_daily, tpex_daily_meta = fetch_json(URLS["tpex_daily"])
    tpex_trade, tpex_trade_meta = fetch_json(URLS["tpex_trade"])
    tpex_underlying, tpex_underlying_meta = fetch_json(URLS["tpex_underlying"])

    twse_status = {
        "source_ok": bool(twse_basic_meta.get("ok") and twse_mi_meta.get("ok")),
        "source_urls": [URLS["twse_basic"], URLS["twse_daily"], URLS["twse_underlying"], twse_mi_meta.get("url")],
    }
    tpex_status = {
        "source_ok": bool(tpex_issue_meta.get("ok") and tpex_daily_meta.get("ok")),
        "source_urls": [URLS["tpex_issue"], URLS["tpex_daily"], URLS["tpex_trade"], URLS["tpex_underlying"]],
    }

    twse = build_twse(execution_date, close_date, twse_basic, twse_daily, twse_underlying, mi, twse_status)
    tpex = build_tpex(execution_date, close_date, tpex_issue, tpex_daily, tpex_trade, tpex_underlying, tpex_status)
    rows = twse["rows"] + tpex["rows"]

    calls = summarize_candidates(rows, "認購")
    puts = summarize_candidates(rows, "認售")

    markets_complete = twse_status["mother_complete"] and tpex_status["mother_complete"]
    hard_complete = twse_status["hard_fields_complete"] and tpex_status["hard_fields_complete"]
    certifiable = bool(markets_complete and hard_complete)
    if certifiable and not calls and not puts:
        certification_message = "本次 0 檔符合"
    elif certifiable:
        certification_message = f"全市場硬條件已完成認證；認購 {len(calls)} 檔、認售 {len(puts)} 檔列入前25正式候選"
    else:
        certification_message = f"本次無法完成全市場認證；目前 {len(calls) + len(puts)} 檔可驗證符合（認購 {len(calls)}、認售 {len(puts)}，各最多25檔）"

    missing_counter = Counter()
    fail_counter = Counter()
    for r in rows:
        missing_counter.update(r["hard_missing"])
        fail_counter.update(r["hard_failed"])

    result = {
        "schema_version": 2,
        "pipeline": {
            "generated_at_asia_taipei": now.isoformat(),
            "intended_heartbeat_time": "08:00 Asia/Taipei",
            "execution_date": execution_date.isoformat(),
            "market_state": market_state(execution_date, holidays),
            "last_completed_trading_date": close_date.isoformat(),
            "price_nature": "最近官方最後收盤價",
            "transport_note": "本檔僅為官方資料的正規化/縮小傳輸層；市場數值來源仍為 TWSE/TPEx 官方端點。",
            "warrant_platform": {
                "url": URLS["warrant_platform"],
                "integration_status": "尚未取得穩定機器可讀端點；價差、官方流動性/造市品質、Delta目前不以替代值冒充",
            },
            "quality_score_algorithm": "內部品質分≠流動性分數：硬條件通過50分；方向化價內外最多20分；成交量最多10分；成交金額最多10分；低溢價最多10分。價差/官方流動性分數/Delta未知時不加分。",
            "volume_unit": "張；若官方端點提供成交單位數，依每張1000單位換算",
            "certification_message": certification_message,
            "all_market_certifiable": certifiable,
        },
        "data_status": {
            "twse": twse_status,
            "tpex": tpex_status,
            "hard_required_missing_counts": dict(missing_counter),
            "hard_exclusion_counts": dict(fail_counter),
            "endpoint_runtime": {
                "twse_holiday": endpoint_entry(holidays_meta, holidays_raw),
                "twse_basic": endpoint_entry(twse_basic_meta, twse_basic),
                "twse_daily": endpoint_entry(twse_daily_meta, twse_daily),
                "twse_underlying": endpoint_entry(twse_underlying_meta, twse_underlying),
                "twse_mi_index": endpoint_entry(twse_mi_meta, twse_mi_raw),
                "tpex_issue": endpoint_entry(tpex_issue_meta, tpex_issue),
                "tpex_daily": endpoint_entry(tpex_daily_meta, tpex_daily),
                "tpex_trade": endpoint_entry(tpex_trade_meta, tpex_trade),
                "tpex_underlying": endpoint_entry(tpex_underlying_meta, tpex_underlying),
            },
        },
        "bullish_calls": calls,
        "bearish_puts": puts,
        "near_miss": {
            "calls": nearest(rows, "認購", 3),
            "puts": nearest(rows, "認售", 3),
        },
        "heartbeat_comparison": {
            "status": "由ChatGPT Heartbeat讀取 latest.json 與 previous.json 後比較",
        },
    }

    if LATEST_PATH.exists():
        PREVIOUS_PATH.write_text(LATEST_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    LATEST_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "certification_message": certification_message,
        "twse": twse_status,
        "tpex": tpex_status,
        "calls": len(calls),
        "puts": len(puts),
        "latest_path": str(LATEST_PATH),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
