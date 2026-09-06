from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import build_heartbeat_v4 as v4

base = v4.base

SCHEMA_VERSION = 5
TOP_N = 100
PRICE_MAX = 1.5
MIN_REMAINING_DAYS_EXCLUSIVE = 180


def current_price_snapshot(row: dict[str, Any], close_date: date) -> tuple[str | None, str | None]:
    snapshot_date = row.get("price_snapshot_date")
    snapshot_mode = row.get("price_snapshot_mode")
    if row.get("warrant_close") is not None and not snapshot_date:
        snapshot_date = close_date.isoformat()
        snapshot_mode = "最近完整交易日官方收盤"
    return snapshot_date, snapshot_mode


def research_fields(row: dict[str, Any]) -> dict[str, Any]:
    strike = row.get("strike")
    ratio = row.get("exercise_ratio")
    wc = row.get("warrant_close")
    uc = row.get("underlying_close")
    kind = row.get("type")

    premium_pct = row.get("premium_pct")
    moneyness_pct = row.get("directional_moneyness_pct")
    if (
        premium_pct is None
        and kind in {"認購", "認售"}
        and strike is not None
        and ratio is not None
        and ratio > 0
        and wc is not None
        and uc is not None
        and uc > 0
    ):
        premium_pct = base.premium(kind, strike, wc, ratio, uc)
    if (
        moneyness_pct is None
        and kind in {"認購", "認售"}
        and strike is not None
        and strike > 0
        and uc is not None
    ):
        moneyness_pct = base.directional_moneyness(kind, strike, uc)

    nominal_leverage = None
    if wc is not None and wc > 0 and uc is not None and ratio is not None and ratio > 0:
        nominal_leverage = uc * ratio / wc

    return {
        "premium_pct": base.round_or_none(premium_pct, 4),
        "directional_moneyness_pct": base.round_or_none(moneyness_pct, 4),
        "nominal_leverage": base.round_or_none(nominal_leverage, 4),
        "delta": "未知",
        "effective_leverage": "未知",
        "spread_pct": "未知",
        "liquidity_score": "未知",
        "suspected_broker_involvement": "無法判斷",
    }


def output_ranked_row(row: dict[str, Any], rank: int, execution_date: date, close_date: date) -> dict[str, Any]:
    expiry = row.get("expiry_date")
    remaining_days = (expiry - execution_date).days if expiry is not None else None
    price_snapshot_date, price_snapshot_mode = current_price_snapshot(row, close_date)
    out = {
        "volume_rank": rank,
        "market": row.get("market"),
        "warrant_code": row.get("code"),
        "name": row.get("name"),
        "type": row.get("type"),
        "underlying": row.get("underlying"),
        "issuer": row.get("issuer") or "未知",
        "warrant_close": base.round_or_none(row.get("warrant_close"), 4),
        "price_nature": "最近官方最後收盤價",
        "price_snapshot_date": price_snapshot_date or "未知",
        "price_snapshot_mode": price_snapshot_mode or "未知",
        "expiry_date": base.iso(expiry),
        "remaining_days": remaining_days,
        "volume_lots": row.get("volume_lots"),
        "trade_value_ntd": row.get("trade_value_ntd"),
        "source": row.get("source") or [],
    }
    out.update(research_fields(row))
    return out


def source_date_complete(status: dict[str, Any], key: str, close_date: date) -> bool:
    values = status.get(key) or []
    return close_date.isoformat() in values


def compare_snapshots(previous: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(previous, dict) or not isinstance(previous.get("pure_warrant_top100"), list):
        return {
            "status": "首次執行新純權證Top100 schema／無可比較前次基準",
            "top100_added": [],
            "top100_removed": [],
            "candidate_added": [],
            "candidate_removed": [],
        }

    prev_top = {str(x.get("warrant_code")): x for x in previous.get("pure_warrant_top100", []) if x.get("warrant_code")}
    cur_top = {str(x.get("warrant_code")): x for x in result.get("pure_warrant_top100", []) if x.get("warrant_code")}
    prev_candidates = {
        str(x.get("warrant_code")): x
        for x in (previous.get("bullish_calls", []) + previous.get("bearish_puts", []))
        if x.get("warrant_code")
    }
    cur_candidates = {
        str(x.get("warrant_code")): x
        for x in (result.get("bullish_calls", []) + result.get("bearish_puts", []))
        if x.get("warrant_code")
    }

    rank_changes = []
    for code in sorted(set(prev_top) & set(cur_top)):
        old_rank = prev_top[code].get("volume_rank")
        new_rank = cur_top[code].get("volume_rank")
        if isinstance(old_rank, int) and isinstance(new_rank, int) and abs(new_rank - old_rank) >= 10:
            rank_changes.append({
                "warrant_code": code,
                "name": cur_top[code].get("name"),
                "previous_rank": old_rank,
                "current_rank": new_rank,
                "change": old_rank - new_rank,
            })

    return {
        "status": "已比較前次純權證Top100 snapshot",
        "top100_added": [cur_top[c] for c in sorted(set(cur_top) - set(prev_top), key=lambda c: cur_top[c].get("volume_rank") or 9999)][:25],
        "top100_removed": [prev_top[c] for c in sorted(set(prev_top) - set(cur_top), key=lambda c: prev_top[c].get("volume_rank") or 9999)][:25],
        "candidate_added": [cur_candidates[c] for c in sorted(set(cur_candidates) - set(prev_candidates), key=lambda c: cur_candidates[c].get("volume_rank") or 9999)][:25],
        "candidate_removed": [prev_candidates[c] for c in sorted(set(prev_candidates) - set(cur_candidates), key=lambda c: prev_candidates[c].get("volume_rank") or 9999)][:25],
        "rank_changes_ge_10": rank_changes[:25],
    }


def main() -> None:
    now = datetime.now(base.TZ)
    execution_date = now.date()

    previous_latest: dict[str, Any] | None = None
    if base.LATEST_PATH.exists():
        try:
            previous_latest = json.loads(base.LATEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            previous_latest = None

    holidays_raw, holidays_meta = base.fetch_json(base.URLS["twse_holiday"])
    holidays = base.holiday_set(holidays_raw)
    close_date = base.last_completed_trading_day(execution_date, holidays)

    twse_basic, twse_basic_meta = base.fetch_json(base.URLS["twse_basic"])
    twse_daily, twse_daily_meta = base.fetch_json(base.URLS["twse_daily"])
    twse_underlying, twse_underlying_meta = base.fetch_json(base.URLS["twse_underlying"])
    twse_mi_raw, twse_mi_meta = base.fetch_json(
        base.URLS["twse_mi_index"],
        {"date": close_date.strftime("%Y%m%d"), "type": "ALL", "response": "json"},
    )
    mi = base.parse_twse_mi(twse_mi_raw)

    tpex_issue, tpex_issue_meta = base.fetch_json(base.URLS["tpex_issue"])
    tpex_daily, tpex_daily_meta = base.fetch_json(base.URLS["tpex_daily"])
    tpex_trade, tpex_trade_meta = base.fetch_json(base.URLS["tpex_trade"])
    tpex_underlying, tpex_underlying_meta = base.fetch_json(base.URLS["tpex_underlying"])

    twse_status: dict[str, Any] = {
        "source_ok": bool(twse_basic_meta.get("ok") and twse_daily_meta.get("ok") and twse_mi_meta.get("ok")),
        "source_urls": [base.URLS["twse_basic"], base.URLS["twse_daily"], base.URLS["twse_underlying"], twse_mi_meta.get("url")],
    }
    tpex_status: dict[str, Any] = {
        "source_ok": bool(tpex_issue_meta.get("ok") and tpex_daily_meta.get("ok") and tpex_trade_meta.get("ok")),
        "source_urls": [base.URLS["tpex_issue"], base.URLS["tpex_daily"], base.URLS["tpex_trade"], base.URLS["tpex_underlying"]],
    }

    twse = base.build_twse(execution_date, close_date, twse_basic, twse_daily, twse_underlying, mi, twse_status)
    tpex = base.build_tpex(execution_date, close_date, tpex_issue, tpex_daily, tpex_trade, tpex_underlying, tpex_status)
    rows = twse["rows"] + tpex["rows"]

    twse_known_volume = sum(1 for r in twse["rows"] if r.get("volume_lots") is not None)
    tpex_known_volume = sum(1 for r in tpex["rows"] if r.get("volume_lots") is not None)
    twse_status["volume_known_count"] = twse_known_volume
    twse_status["volume_unknown_count"] = max(0, twse_status.get("mother_count", 0) - twse_known_volume)
    tpex_status["volume_known_count"] = tpex_known_volume
    tpex_status["volume_unknown_count"] = max(0, tpex_status.get("mother_count", 0) - tpex_known_volume)

    twse_volume_feed_complete = bool(
        twse_status.get("mother_complete")
        and twse_daily_meta.get("ok")
        and source_date_complete(twse_status, "daily_data_dates", close_date)
    )
    tpex_volume_feed_complete = bool(
        tpex_status.get("mother_complete")
        and tpex_trade_meta.get("ok")
        and source_date_complete(tpex_status, "trade_data_dates", close_date)
    )
    twse_status["pure_warrant_volume_feed_complete"] = twse_volume_feed_complete
    tpex_status["pure_warrant_volume_feed_complete"] = tpex_volume_feed_complete

    rankable = [r for r in rows if r.get("volume_lots") is not None]
    rankable.sort(
        key=lambda r: (
            -(r.get("volume_lots") or 0),
            -(r.get("trade_value_ntd") or 0),
            str(r.get("code") or ""),
        )
    )
    top100_rows = rankable[:TOP_N]
    top100 = [output_ranked_row(r, i + 1, execution_date, close_date) for i, r in enumerate(top100_rows)]

    all_market_top100_certifiable = bool(
        twse_volume_feed_complete
        and tpex_volume_feed_complete
        and len(top100_rows) == TOP_N
    )

    price_pass_rows: list[tuple[int, dict[str, Any]]] = []
    price_missing_count = 0
    price_fail_count = 0
    for rank, row in enumerate(top100_rows, start=1):
        wc = row.get("warrant_close")
        if wc is None:
            price_missing_count += 1
        elif wc <= PRICE_MAX:
            price_pass_rows.append((rank, row))
        else:
            price_fail_count += 1

    final_rows: list[tuple[int, dict[str, Any]]] = []
    expiry_missing_count = 0
    expiry_fail_count = 0
    for rank, row in price_pass_rows:
        expiry = row.get("expiry_date")
        if expiry is None:
            expiry_missing_count += 1
            continue
        remaining_days = (expiry - execution_date).days
        if remaining_days > MIN_REMAINING_DAYS_EXCLUSIVE:
            final_rows.append((rank, row))
        else:
            expiry_fail_count += 1

    final_output = [output_ranked_row(r, rank, execution_date, close_date) for rank, r in final_rows]
    calls = [x for x in final_output if x.get("type") == "認購"][:25]
    puts = [x for x in final_output if x.get("type") == "認售"][:25]

    top100_price_complete = price_missing_count == 0
    top100_expiry_complete = expiry_missing_count == 0
    final_count_certifiable = bool(all_market_top100_certifiable and top100_price_complete and top100_expiry_complete)

    if not all_market_top100_certifiable:
        certification_message = f"本次無法完成全市場純權證 Top100 認證；目前 {len(final_output)} 檔可驗證符合"
    elif not final_count_certifiable:
        certification_message = f"全市場純權證 Top100 已認證，但價格／期限欄位仍有缺失；目前 {len(final_output)} 檔可驗證符合"
    else:
        certification_message = f"全市場純權證 Top100 已認證；正式候選 {len(final_output)} 檔（認購 {sum(1 for x in final_output if x.get('type') == '認購')}、認售 {sum(1 for x in final_output if x.get('type') == '認售')}）"

    top100_summary = {
        "ranking_basis": "TWSE+TPEx 純權證本身最近完整交易日官方成交量（張）",
        "count": len(top100),
        "highest_volume_lots": top100[0]["volume_lots"] if top100 else None,
        "lowest_volume_lots": top100[-1]["volume_lots"] if len(top100) == TOP_N else None,
        "twse_count": sum(1 for x in top100 if x.get("market") == "TWSE"),
        "tpex_count": sum(1 for x in top100 if x.get("market") == "TPEx"),
        "call_count": sum(1 for x in top100 if x.get("type") == "認購"),
        "put_count": sum(1 for x in top100 if x.get("type") == "認售"),
    }

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "pipeline": {
            "generated_at_asia_taipei": now.isoformat(),
            "intended_heartbeat_time": "08:00 Asia/Taipei",
            "execution_date": execution_date.isoformat(),
            "market_state": base.market_state(execution_date, holidays),
            "last_completed_trading_date": close_date.isoformat(),
            "price_nature": "最近官方最後收盤價",
            "transport_note": "本檔僅為 TWSE/TPEx 官方資料的正規化／compact snapshot；不使用第三方行情產生正式排名。",
            "warrant_platform": {
                "url": base.URLS["warrant_platform"],
                "integration_status": "尚未取得穩定機器可讀端點；價差、官方流動性/造市品質、Delta目前不以替代值冒充",
            },
            "volume_unit": "張",
            "certification_message": certification_message,
            "all_market_pure_warrant_top100_certifiable": all_market_top100_certifiable,
            "final_count_certifiable": final_count_certifiable,
        },
        "data_status": {
            "twse": twse_status,
            "tpex": tpex_status,
            "endpoint_runtime": {
                "twse_holiday": base.endpoint_entry(holidays_meta, holidays_raw),
                "twse_basic": base.endpoint_entry(twse_basic_meta, twse_basic),
                "twse_daily": base.endpoint_entry(twse_daily_meta, twse_daily),
                "twse_underlying": base.endpoint_entry(twse_underlying_meta, twse_underlying),
                "twse_mi_index": base.endpoint_entry(twse_mi_meta, twse_mi_raw),
                "tpex_issue": base.endpoint_entry(tpex_issue_meta, tpex_issue),
                "tpex_daily": base.endpoint_entry(tpex_daily_meta, tpex_daily),
                "tpex_trade": base.endpoint_entry(tpex_trade_meta, tpex_trade),
                "tpex_underlying": base.endpoint_entry(tpex_underlying_meta, tpex_underlying),
            },
        },
        "pure_warrant_top100_summary": top100_summary,
        "pure_warrant_top100": top100,
        "filter_funnel": {
            "pure_warrant_mother_count": twse_status.get("mother_count", 0) + tpex_status.get("mother_count", 0),
            "twse_mother_count": twse_status.get("mother_count", 0),
            "tpex_mother_count": tpex_status.get("mother_count", 0),
            "top100_count": len(top100),
            "top100_price_le_1_5_count": len(price_pass_rows),
            "top100_price_missing_count": price_missing_count,
            "top100_price_gt_1_5_count": price_fail_count,
            "final_remaining_days_gt_180_count": len(final_output),
            "expiry_missing_after_price_filter_count": expiry_missing_count,
            "expiry_le_180_after_price_filter_count": expiry_fail_count,
            "final_call_count": sum(1 for x in final_output if x.get("type") == "認購"),
            "final_put_count": sum(1 for x in final_output if x.get("type") == "認售"),
        },
        "bullish_calls": calls,
        "bearish_puts": puts,
        "formal_candidates_all": final_output,
    }
    result["heartbeat_comparison"] = compare_snapshots(previous_latest, result)

    if base.LATEST_PATH.exists():
        base.PREVIOUS_PATH.write_text(base.LATEST_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    base.LATEST_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "certification_message": certification_message,
        "execution_date": execution_date.isoformat(),
        "last_completed_trading_date": close_date.isoformat(),
        "twse_mother": twse_status.get("mother_count"),
        "tpex_mother": tpex_status.get("mother_count"),
        "top100": len(top100),
        "price_pass": len(price_pass_rows),
        "final": len(final_output),
        "calls": len(calls),
        "puts": len(puts),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
