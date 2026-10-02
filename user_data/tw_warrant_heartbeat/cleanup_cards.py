from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CARDS = ROOT / "cards"
KEEP_DAYS = 30


def main() -> None:
    CARDS.mkdir(parents=True, exist_ok=True)
    today = date.today()
    removed = []
    for path in sorted(CARDS.glob("????-??-??.svg")):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - file_date).days > KEEP_DAYS:
            path.unlink()
            removed.append(path.name)
    print("Removed old cards:", ", ".join(removed) if removed else "none")


if __name__ == "__main__":
    main()
