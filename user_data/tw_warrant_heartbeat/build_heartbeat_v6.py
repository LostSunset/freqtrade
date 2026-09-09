from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import build_heartbeat_v5 as v5

base = v5.base
_original_fetch_json = base.fetch_json

CACHE_DIR = Path(__file__).resolve().parent / ".source_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Cache is restored/saved by GitHub Actions cache, not committed to git.
# It contains only official-source reference payloads used to survive transient endpoint failures.
CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
OUTER_RETRIES = 2

_state: dict[str, Any] = {
    "holidays": None,
    "mi": {},
    "tpex_daily": None,
}


def _payload_ok(data: Any) -> bool:
    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        return bool(data)
    return False


def _retry_official(url: str, params: dict[str, str] | None = None):
    last_data = None
    last_meta: dict[str, Any] = {"ok": False, "url": url}
    started = time.monotonic()
    for outer in range(1, OUTER_RETRIES + 1):
        data, meta = _original_fetch_json(url, params)
        last_data, last_meta = data, dict(meta)
        if meta.get("ok") and _payload_ok(data):
            last_meta["outer_attempt"] = outer
            last_meta["resilient_elapsed_seconds"] = round(time.monotonic() - started, 3)
            return data, last_meta
        if outer < OUTER_RETRIES:
            time.sleep(4 * outer)
    last_meta["outer_attempt"] = OUTER_RETRIES
    last_meta["resilient_elapsed_seconds"] = round(time.monotonic() - started, 3)
    return last_data, last_meta


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.json.gz"


def _save_cache(name: str, data: Any, source_url: str) -> None:
    if not _payload_ok(data):
        return
    payload = {
        "saved_at": datetime.now(base.TZ).isoformat(),
        "source_url": source_url,
        "data": data,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(_cache_path(name), "wb", compresslevel=9) as fh:
        fh.write(raw)


def _load_cache(name: str):
    path = _cache_path(name)
    if not path.exists():
        return None, {"ok": False, "cache": name, "error": "cache_missing"}
    age = max(0.0, time.time() - path.stat().st_mtime)
    if age > CACHE_MAX_AGE_SECONDS:
        return None, {"ok": False, "cache": name, "error": "cache_expired", "cache_age_seconds": round(age, 1)}
    try:
        with gzip.open(path, "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
        data = payload.get("data")
        if not _payload_ok(data):
            raise ValueError("cached payload empty")
        return data, {
            "ok": True,
            "cache": name,
            "cache_age_seconds": round(age, 1),
            "cache_saved_at": payload.get("saved_at"),
            "cache_source_url": payload.get("source_url"),
        }
    except Exception as exc:
        return None, {"ok": False, "cache": name, "error": f"{type(exc).__name__}: {exc}"}


def _close_date():
    holidays = base.holiday_set(_state.get("holidays"))
    return base.last_completed_trading_day(datetime.now(base.TZ).date(), holidays)


def _fetch_mi_for_close():
    close_date = _close_date()
    key = close_date.isoformat()
    cached = _state["mi"].get(key)
    if cached:
        return cached
    data, meta = _retry_official(
        base.URLS["twse_mi_index"],
        {"date": close_date.strftime("%Y%m%d"), "type": "ALL", "response": "json"},
    )
    mi = base.parse_twse_mi(data)
    value = (data, meta, mi, close_date)
    _state["mi"][key] = value
    return value


def _warrant_like_name(value: Any) -> bool:
    name = base.clean_text(value).replace(" ", "")
    # TWSE listed warrants/bull-bear certificates conventionally end with 購/售/牛/熊 + 2 digits.
    return bool(re.search(r"(?:購|售|牛|熊)\d{2}$", name))


def _reconcile_twse_basic_cache(basic: Any):
    if not isinstance(basic, list):
        return False, {"reason": "cache_not_list"}
    _raw, mi_meta, mi, close_date = _fetch_mi_for_close()
    if not mi_meta.get("ok") or not mi.get("security_by_code"):
        return False, {"reason": "mi_index_unavailable"}

    current_warrant_codes = {
        str(code)
        for code, item in mi["security_by_code"].items()
        if _warrant_like_name(item.get("name"))
    }
    cached_codes = {
        base.clean_text(row.get("權證代號"))
        for row in basic
        if isinstance(row, dict) and base.clean_text(row.get("權證類型")) in {"認購", "認售"}
    }
    cached_codes.discard("")
    unknown_current = sorted(current_warrant_codes - cached_codes)

    # Require a large current warrant set and zero current listed warrant codes missing from cache.
    # Extra cached rows are acceptable because they receive no current MI_INDEX volume and cannot enter Top100.
    ok = len(current_warrant_codes) >= 20000 and not unknown_current
    return ok, {
        "close_date": close_date.isoformat(),
        "mi_warrant_count": len(current_warrant_codes),
        "cached_reference_count": len(cached_codes),
        "unknown_current_warrant_count": len(unknown_current),
        "unknown_current_warrant_sample": unknown_current[:20],
    }


def _synthetic_twse_daily_from_mi(original_meta: dict[str, Any]):
    _raw, mi_meta, mi, close_date = _fetch_mi_for_close()
    if not mi_meta.get("ok") or not mi.get("security_by_code"):
        return None, original_meta
    rows = []
    date_text = close_date.strftime("%Y%m%d")
    for code, item in mi["security_by_code"].items():
        units = item.get("volume_num")
        value = item.get("value_num")
        if units is None and value is None:
            continue
        rows.append({
            "交易日期": date_text,
            "出表日期": date_text,
            "權證代號": str(code),
            "權證名稱": item.get("name") or "",
            # v3 normalizer divides this field by 1000, so retain MI_INDEX units here.
            "成交張數": 0 if units is None else units,
            "成交金額": 0 if value is None else value,
        })
    if not rows:
        return None, original_meta
    return rows, {
        "ok": True,
        "url": mi_meta.get("url"),
        "official_fallback": True,
        "fallback_source": "TWSE MI_INDEX type=ALL",
        "fallback_for": base.URLS["twse_daily"],
        "original_error": original_meta.get("error"),
        "rows": len(rows),
    }


def _reconcile_tpex_issue_cache(issue: Any):
    if not isinstance(issue, list):
        return False, {"reason": "cache_not_list"}
    daily = _state.get("tpex_daily")
    daily_meta = None
    if not isinstance(daily, list):
        daily, daily_meta = _retry_official(base.URLS["tpex_daily"])
        if isinstance(daily, list):
            _state["tpex_daily"] = daily
    if not isinstance(daily, list):
        return False, {"reason": "current_tpex_daily_unavailable", "daily_meta": daily_meta}
    current_codes = {base.clean_text(x.get("Code")) for x in daily if isinstance(x, dict)}
    issue_codes = {base.clean_text(x.get("Code")) for x in issue if isinstance(x, dict)}
    current_codes.discard("")
    issue_codes.discard("")
    missing = sorted(current_codes - issue_codes)
    ok = len(current_codes) >= 5000 and not missing
    return ok, {
        "current_daily_count": len(current_codes),
        "cached_issue_count": len(issue_codes),
        "unknown_current_warrant_count": len(missing),
        "unknown_current_warrant_sample": missing[:20],
    }


def _synthetic_tpex_trade_from_daily(original_meta: dict[str, Any]):
    daily = _state.get("tpex_daily")
    if not isinstance(daily, list):
        daily, daily_meta = _retry_official(base.URLS["tpex_daily"])
        if isinstance(daily, list):
            _state["tpex_daily"] = daily
        else:
            return None, original_meta
    rows = []
    for q in daily:
        if not isinstance(q, dict):
            continue
        code = base.clean_text(q.get("Code"))
        if not code:
            continue
        rows.append({
            "權證代號": code,
            "交易日期": q.get("Date"),
            "成交數量": q.get("TradeVol."),
            "成交金額": q.get("TradeValue"),
        })
    if not rows:
        return None, original_meta
    return rows, {
        "ok": True,
        "url": base.URLS["tpex_daily"],
        "official_fallback": True,
        "fallback_source": "TPEx tpex_warrant_daily_quts",
        "fallback_for": base.URLS["tpex_trade"],
        "original_error": original_meta.get("error"),
        "rows": len(rows),
    }


def resilient_fetch_json(url: str, params: dict[str, str] | None = None):
    data, meta = _retry_official(url, params)
    if meta.get("ok") and _payload_ok(data):
        if url == base.URLS["twse_holiday"]:
            _state["holidays"] = data
            _save_cache("twse_holidays", data, url)
        elif url == base.URLS["twse_basic"]:
            _save_cache("twse_basic", data, url)
        elif url == base.URLS["tpex_issue"]:
            _save_cache("tpex_issue", data, url)
        elif url == base.URLS["tpex_daily"]:
            _state["tpex_daily"] = data
        return data, meta

    # Holiday calendar: cached official calendar is sufficient for resolving the previous trading date.
    if url == base.URLS["twse_holiday"]:
        cached, cmeta = _load_cache("twse_holidays")
        if cached is not None:
            _state["holidays"] = cached
            return cached, {**cmeta, "official_fallback": True, "fallback_for": url, "original_error": meta.get("error")}

    # TWSE basic data: cache is accepted only after reconciling every current MI_INDEX warrant-like code.
    if url == base.URLS["twse_basic"]:
        cached, cmeta = _load_cache("twse_basic")
        if cached is not None:
            ok, reconciliation = _reconcile_twse_basic_cache(cached)
            if ok:
                return cached, {
                    **cmeta,
                    "ok": True,
                    "official_fallback": True,
                    "fallback_source": "cached official t187ap37_L + current TWSE MI_INDEX reconciliation",
                    "fallback_for": url,
                    "reconciliation": reconciliation,
                    "original_error": meta.get("error"),
                }

    # TWSE daily turnover: MI_INDEX is an independent official same-day source with code/volume/value.
    if url == base.URLS["twse_daily"]:
        fallback, fmeta = _synthetic_twse_daily_from_mi(meta)
        if fallback is not None:
            return fallback, fmeta

    # TPEx daily quotes: official mirror endpoint.
    if url == base.URLS["tpex_daily"]:
        mirror_url = "https://www.tpex.org.tw/openapi/v1/tpex_warrant_quts"
        mirror, mmeta = _retry_official(mirror_url)
        if isinstance(mirror, list) and mirror and isinstance(mirror[0], dict) and "Code" in mirror[0]:
            _state["tpex_daily"] = mirror
            return mirror, {
                **mmeta,
                "ok": True,
                "official_fallback": True,
                "fallback_source": "TPEx tpex_warrant_quts",
                "fallback_for": url,
                "original_error": meta.get("error"),
            }

    # TPEx issue reference: cached official issue file is accepted only if it covers every current quote code.
    if url == base.URLS["tpex_issue"]:
        cached, cmeta = _load_cache("tpex_issue")
        if cached is not None:
            ok, reconciliation = _reconcile_tpex_issue_cache(cached)
            if ok:
                return cached, {
                    **cmeta,
                    "ok": True,
                    "official_fallback": True,
                    "fallback_source": "cached official tpex_warrant_issue + current TPEx quote reconciliation",
                    "fallback_for": url,
                    "reconciliation": reconciliation,
                    "original_error": meta.get("error"),
                }

    # TPEx turnover: daily quote file already carries TradeVol./TradeValue and is official.
    if url == base.URLS["tpex_trade"]:
        fallback, fmeta = _synthetic_tpex_trade_from_daily(meta)
        if fallback is not None:
            return fallback, fmeta

    return data, meta


# v5.main resolves base.fetch_json dynamically. Replace transport/fallback only;
# ranking, filters, formulas and certification rules remain in v5.
base.fetch_json = resilient_fetch_json


if __name__ == "__main__":
    v5.main()
