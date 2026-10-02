from __future__ import annotations

import html
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CARDS = ROOT / "cards"
LATEST_JSON = ROOT / "latest.json"
MAX_ROWS_PER_SIDE = 25


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def issuer_from_name(name: str) -> str:
    aliases = [
        ("中國信託", "中信"), ("中信", "中信"), ("富邦", "富邦"), ("元大", "元大"),
        ("統一", "統一"), ("國票", "國票"), ("群益", "群益"), ("永豐", "永豐"),
        ("凱基", "凱基"), ("兆豐", "兆豐"), ("台新", "台新"), ("元富", "元富"),
        ("第一金", "第一金"), ("華南", "華南"), ("合庫", "合庫"), ("國泰", "國泰"),
    ]
    for token, label in aliases:
        if token in (name or ""):
            return label
    return "未知"


def text(x: int, y: int, value: Any, size: int = 14, weight: int = 400,
         fill: str = "#26394F", anchor: str = "start") -> str:
    return (
        f'<text x="{x}" y="{y}" '
        'font-family="Noto Sans TC, Microsoft JhengHei, PingFang TC, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">'
        f'{esc(value)}</text>'
    )


def rect(x: int, y: int, w: int, h: int, fill: str, stroke: str = "none", rx: int = 0) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}"/>'


def rows_from(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = data.get(key) or []
    return rows if isinstance(rows, list) else []


def render(data: dict[str, Any]) -> str:
    pipe = data.get("pipeline", {})
    status = data.get("data_status", {})
    funnel = data.get("filter_funnel", {})
    summary = data.get("pure_warrant_top100_summary", {})
    calls = rows_from(data, "bullish_calls")[:MAX_ROWS_PER_SIDE]
    puts = rows_from(data, "bearish_puts")[:MAX_ROWS_PER_SIDE]

    execution_date = pipe.get("execution_date") or date.today().isoformat()
    close_date = pipe.get("last_completed_trading_date") or "未知"
    market_state = pipe.get("market_state") or "未知"
    certified = bool(pipe.get("all_market_pure_warrant_top100_certifiable"))
    twse = status.get("twse", {}).get("mother_count")
    tpex = status.get("tpex", {}).get("mother_count")
    total = (twse + tpex) if isinstance(twse, int) and isinstance(tpex, int) else "未知"

    top100 = rows_from(data, "pure_warrant_top100")
    twse_top = summary.get("twse_count", sum(1 for r in top100 if r.get("market") == "TWSE"))
    tpex_top = summary.get("tpex_count", sum(1 for r in top100 if r.get("market") == "TPEx"))
    call_top = summary.get("call_count", sum(1 for r in top100 if r.get("type") == "認購"))
    put_top = summary.get("put_count", sum(1 for r in top100 if r.get("type") == "認售"))

    price_count = funnel.get("top100_price_le_1_5_count", "未知")
    final_count = funnel.get("final_remaining_days_gt_180_count", len(calls) + len(puts))
    calls_total = len(rows_from(data, "bullish_calls"))
    puts_total = len(rows_from(data, "bearish_puts"))

    rows_count = max(len(calls), len(puts), 1)
    body_h = max(840, 130 + rows_count * 34)
    H = 495 + body_h + 170
    W = 1200
    parts: list[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    parts.append(rect(0, 0, W, H, "#F4F6F9"))
    parts.append(rect(30, 25, 1140, 130, "#10243E", rx=22))
    parts.append(text(64, 78, "台股權證每日篩選 Heartbeat", 40, 800, "#FFFFFF"))
    parts.append(text(64, 116, "研究用途｜最近官方最後收盤價｜成交量僅指權證本身", 18, 500, "#CBD6E5"))
    parts.append(text(1136, 116, execution_date, 18, 700, "#FFFFFF", "end"))

    parts.append(rect(30, 180, 1140, 155, "#FFFFFF", "#D7DFE8", 18))
    parts.append(text(55, 218, "資料狀態", 24, 800, "#10243E"))
    items = [
        (55, "最近完整交易日", close_date, "#10243E"),
        (300, "市場狀態", market_state, "#B45E1C"),
        (520, "全市場認證", "是" if certified else "否", "#19704A" if certified else "#A34C1F"),
        (710, "純權證母體", f"{total:,}" if isinstance(total, int) else total, "#10243E"),
        (930, "Top100", len(top100), "#10243E"),
    ]
    for x, label, value, color in items:
        parts.append(text(x, 248, label, 15, 650, "#66768A"))
        parts.append(text(x, 282, value, 23, 800, color))
    parts.append(text(55, 317, f"TWSE {twse}｜TPEx {tpex}｜Top100：TWSE {twse_top} / TPEx {tpex_top}｜認購 {call_top} / 認售 {put_top}", 15, 650, "#66768A"))

    parts.append(rect(30, 360, 1140, 110, "#FFFFFF", "#D7DFE8", 18))
    parts.append(text(55, 398, "篩選漏斗", 24, 800, "#10243E"))
    for x, value, label in [
        (55, len(top100), "Top100"), (280, price_count, "收盤價≤1.5"),
        (535, final_count, "期限>180天"), (820, f"{calls_total} / {puts_total}", "認購 / 認售")
    ]:
        parts.append(text(x, 440, value, 23, 800, "#173B60"))
        parts.append(text(x + (40 if x < 800 else 100), 440, label, 15, 650, "#66768A"))

    body_y = 495
    parts.append(rect(30, body_y, 1140, body_h, "#FFFFFF", "#EAD8CA", 18))
    parts.append(text(55, body_y + 40, f"看多認購｜前 {len(calls)} 檔（正式認購共 {calls_total} 檔）", 23, 800, "#A34C1F"))
    parts.append(text(620, body_y + 40, f"看空認售｜前 {len(puts)} 檔（正式認售共 {puts_total} 檔）", 23, 800, "#245B88"))

    def draw_table(rows: list[dict[str, Any]], x0: int, y0: int, width: int) -> None:
        parts.append(text(x0, y0, "排名", 13, 700, "#5B697C"))
        parts.append(text(x0 + 48, y0, "代號 / 名稱", 13, 700, "#5B697C"))
        parts.append(text(x0 + width - 180, y0, "收盤", 13, 700, "#5B697C", "end"))
        parts.append(text(x0 + width - 95, y0, "天數", 13, 700, "#5B697C", "end"))
        parts.append(text(x0 + width, y0, "成交量", 13, 700, "#5B697C", "end"))
        y = y0 + 32
        if not rows:
            parts.append(text(x0, y, "無正式候選", 14, 600, "#8291A4"))
            return
        for i, row in enumerate(rows):
            if i % 2:
                parts.append(rect(x0 - 8, y - 22, width + 8, 29, "#FAFBFD", rx=5))
            rank = row.get("volume_rank", "")
            code = row.get("warrant_code", "")
            name = row.get("name", "")
            close = row.get("warrant_close")
            days = row.get("remaining_days", "")
            vol = row.get("volume_lots")
            label = f"{code} {name}"
            if len(label) > 23:
                label = label[:22] + "…"
            parts.append(text(x0, y, rank, 13, 700))
            parts.append(text(x0 + 48, y, label, 13, 600, "#173B60"))
            parts.append(text(x0 + width - 180, y, f"{close:.2f}" if isinstance(close, (int, float)) else "未知", 13, 700, "#26394F", "end"))
            parts.append(text(x0 + width - 95, y, days, 13, 700, "#26394F", "end"))
            parts.append(text(x0 + width, y, f"{vol:,}" if isinstance(vol, (int, float)) else "未知", 13, 700, "#26394F", "end"))
            y += 34

    draw_table(calls, 55, body_y + 76, 500)
    draw_table(puts, 620, body_y + 76, 500)

    footer_y = body_y + body_h + 25
    parts.append(rect(30, footer_y, 1140, 105, "#FFFFFF", "#D7DFE8", 16))
    comparison = data.get("heartbeat_comparison", {})
    added = len(comparison.get("candidate_added") or [])
    removed = len(comparison.get("candidate_removed") or [])
    parts.append(text(55, footer_y + 34, f"Heartbeat：正式候選新增 {added}｜退出 {removed}｜疑似券商介入：無法判斷", 15, 650, "#66768A"))
    parts.append(text(55, footer_y + 62, f"price_snapshot_date={close_date}｜全市場認證={'是' if certified else '否'}｜SVG 純文字/幾何、無 base64 圖片或字型", 15, 500, "#66768A"))
    parts.append(text(55, footer_y + 88, "券商名稱僅供顯示；正式篩選資格只依 Top100、收盤價≤1.5、剩餘期限>180天。", 14, 500, "#8291A4"))
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    data = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
    execution_date = data.get("pipeline", {}).get("execution_date")
    if not execution_date:
        raise SystemExit("latest.json missing pipeline.execution_date")

    CARDS.mkdir(parents=True, exist_ok=True)
    svg = render(data)
    encoded = svg.encode("utf-8")
    if len(encoded) > 100_000:
        raise SystemExit(f"SVG exceeds 100 KB target: {len(encoded)} bytes")

    daily = CARDS / f"{execution_date}.svg"
    latest = CARDS / "latest.svg"
    daily.write_text(svg, encoding="utf-8")
    shutil.copyfile(daily, latest)
    print(f"Rendered {daily} ({len(encoded)} bytes)")
    print(f"Updated {latest}")


if __name__ == "__main__":
    main()
