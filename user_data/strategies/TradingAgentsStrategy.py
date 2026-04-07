"""Freqtrade strategy that consumes signals from TradingAgents.

TradingAgents runs separately (via signal_service.py) and writes signals to
a JSON file. This strategy reads those signals and translates them into
Freqtrade entry/exit decisions.

Signal mapping:
    BUY / OVERWEIGHT  ->  enter_long = 1
    SELL / UNDERWEIGHT -> exit_long = 1
    HOLD              ->  no action

Setup:
    1. Run signal_service.py to generate signals
    2. Configure this strategy in your freqtrade config
    3. Start freqtrade with: freqtrade trade --strategy TradingAgentsStrategy
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from freqtrade.strategy import IStrategy
import pandas as pd

logger = logging.getLogger(__name__)

# Default path to the signals file written by signal_service.py
_DEFAULT_SIGNALS_PATH = Path(__file__).resolve().parents[3] / "signals" / "latest_signals.json"


class TradingAgentsStrategy(IStrategy):
    """Strategy that reads pre-computed TradingAgents signals."""

    # Strategy interface version
    INTERFACE_VERSION = 3

    # Timeframe - daily since TradingAgents produces daily signals
    timeframe = "1d"

    # ROI: take profit at 10% immediately, let signals drive exits
    minimal_roi = {
        "0": 0.10,
        "1440": 0.05,   # 5% after 1 day
        "4320": 0.02,   # 2% after 3 days
    }

    # Stoploss: -8% hard stop as safety net
    stoploss = -0.08

    # Trailing stop for protection
    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

    # Signal staleness: ignore signals older than this many hours
    max_signal_age_hours = 48

    # Number of candles needed before strategy starts
    startup_candle_count = 1

    # Disable short trading by default
    can_short = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._signals_path = Path(
            config.get("tradingagents_signals_path", str(_DEFAULT_SIGNALS_PATH))
        )
        self._cached_signals: Optional[dict] = None
        self._cache_time: Optional[datetime] = None
        logger.info("TradingAgentsStrategy: signals path = %s", self._signals_path)

    def _load_signals(self) -> dict:
        """Load signals from JSON, with caching (refresh every 60s)."""
        now = datetime.now(timezone.utc)

        # Use cache if fresh enough
        if (
            self._cached_signals is not None
            and self._cache_time is not None
            and (now - self._cache_time).total_seconds() < 60
        ):
            return self._cached_signals

        if not self._signals_path.exists():
            logger.warning("Signals file not found: %s", self._signals_path)
            return {}

        try:
            with open(self._signals_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check staleness
            updated_at = data.get("updated_at", "")
            if updated_at:
                signal_time = datetime.fromisoformat(updated_at)
                age_hours = (now - signal_time).total_seconds() / 3600
                if age_hours > self.max_signal_age_hours:
                    logger.warning(
                        "Signals are %.1f hours old (max: %d), ignoring",
                        age_hours, self.max_signal_age_hours,
                    )
                    return {}

            self._cached_signals = data.get("signals", {})
            self._cache_time = now
            return self._cached_signals

        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load signals: %s", e)
            return {}

    def _get_signal_for_pair(self, pair: str) -> str:
        """Get the TradingAgents signal for a trading pair.

        Maps Freqtrade pair format (e.g., "BTC/USDT") to ticker (e.g., "BTC").
        Returns one of: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL
        """
        signals = self._load_signals()
        if not signals:
            return "HOLD"

        # Try multiple ticker formats
        base = pair.split("/")[0]  # "BTC/USDT" -> "BTC"
        for key in [base, pair, pair.replace("/", "")]:
            if key in signals:
                return signals[key].get("decision", "HOLD")

        return "HOLD"

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """No indicators needed - signals come from TradingAgents."""
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """Set entry signals based on TradingAgents decision."""
        signal = self._get_signal_for_pair(metadata["pair"])

        # BUY or OVERWEIGHT -> enter long
        if signal in ("BUY", "OVERWEIGHT"):
            # Only signal on the last candle
            dataframe.loc[dataframe.index[-1], "enter_long"] = 1
            dataframe.loc[dataframe.index[-1], "enter_tag"] = f"ta_{signal.lower()}"
            logger.info("ENTER signal for %s: %s", metadata["pair"], signal)
        else:
            dataframe["enter_long"] = 0

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """Set exit signals based on TradingAgents decision."""
        signal = self._get_signal_for_pair(metadata["pair"])

        # SELL or UNDERWEIGHT -> exit long
        if signal in ("SELL", "UNDERWEIGHT"):
            dataframe.loc[dataframe.index[-1], "exit_long"] = 1
            dataframe.loc[dataframe.index[-1], "exit_tag"] = f"ta_{signal.lower()}"
            logger.info("EXIT signal for %s: %s", metadata["pair"], signal)
        else:
            dataframe["exit_long"] = 0

        return dataframe

    def custom_stake_amount(self, pair: str, current_time, current_rate: float,
                            proposed_stake: float, min_stake, max_stake,
                            leverage: float, entry_tag, side: str, **kwargs) -> float:
        """Adjust position size based on signal strength."""
        signal = self._get_signal_for_pair(pair)

        if signal == "BUY":
            # Full conviction -> full stake
            return proposed_stake
        elif signal == "OVERWEIGHT":
            # Moderate conviction -> 60% stake
            return proposed_stake * 0.6

        return proposed_stake

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        """Final confirmation before entry - verify signal is still fresh."""
        signals = self._load_signals()
        if not signals:
            logger.warning("No signals available, blocking entry for %s", pair)
            return False
        return True
