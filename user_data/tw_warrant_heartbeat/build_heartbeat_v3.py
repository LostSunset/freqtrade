from __future__ import annotations

import re
from datetime import date

import build_heartbeat as base


_original_twse = base.build_twse
_original_tpex = base.build_tpex


def _source_date(text: str) -> date | None:
    m = re.search(r"\((\d{4}-\d{2}-\d{2})\)", text or "")
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def build_twse_fixed(execution_date, close_date, basic, daily, stock_rows, mi, status):
    result = _original_twse(execution_date, close_date, basic, daily, stock_rows, mi, status)
    # t187ap42_L 欄名雖為「成交張數」，實際數值和成交金額/權證價格交叉驗證後為單位數；
    # 台灣權證每張 1,000 單位，因此統一轉成「張」。MI_INDEX fallback 原程式已除以 1,000。
    daily_codes = set()
    if isinstance(daily, list):
        for item in daily:
            if not isinstance(item, dict):
                continue
            if base.roc_or_gregorian_date(item.get("交易日期")) == close_date:
                code = base.clean_text(item.get("權證代號"))
                if code:
                    daily_codes.add(code)
    for row in result["rows"]:
        if row.get("code") in daily_codes and row.get("volume_lots") is not None:
            row["volume_lots"] = int(row["volume_lots"] // 1000)
    status["volume_normalization"] = "t187ap42_L 成交張數欄依實際單位數/1000轉為張；MI_INDEX成交股數同樣/1000"
    return result


def build_tpex_fixed(execution_date, close_date, issue, daily, trade, underlying_rows, status):
    result = _original_tpex(execution_date, close_date, issue, daily, trade, underlying_rows, status)
    corrected = 0
    for row in result["rows"]:
        sources = row.get("source") or []
        q_date = _source_date(sources[1]) if len(sources) > 1 else None
        # 日報日期若不是本次應使用的最後完整交易日，禁止使用較新的日報價格重建 08:00 結果。
        if q_date != close_date:
            if row.get("warrant_close") is not None or row.get("underlying_close") is not None:
                corrected += 1
            row["warrant_close"] = None
            row["underlying_close"] = None
            row.pop("premium_pct", None)
            row.pop("directional_moneyness_pct", None)
            row.pop("remaining_days", None)
            missing, failed = base.hard_status(row, execution_date)
            row["hard_missing"] = missing
            row["hard_failed"] = failed
    complete = sum(1 for row in result["rows"] if not row["hard_missing"])
    status["hard_fields_complete_count"] = complete
    status["hard_fields_complete"] = bool(status.get("mother_count") and complete == status["mother_count"])
    status["date_guard"] = {
        "required_close_date": close_date.isoformat(),
        "rows_invalidated_due_to_newer_or_stale_daily_date": corrected,
    }
    return result


base.build_twse = build_twse_fixed
base.build_tpex = build_tpex_fixed


if __name__ == "__main__":
    base.main()
