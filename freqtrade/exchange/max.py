"""MAX exchange subclass for Freqtrade.

MAX (MaiCoin Assets eXchange) is a Taiwanese cryptocurrency exchange.
Since ccxt does not include MAX natively, we register a custom ccxt class
at import time so that Freqtrade's exchange loading mechanism works.
"""

import logging

import ccxt
import ccxt.async_support as ccxt_async

from freqtrade.exchange import Exchange
from freqtrade.exchange.exchange_types import FtHas
from freqtrade.exchange.max_ccxt import max as MaxCcxt

logger = logging.getLogger(__name__)

# Register the custom MAX exchange class into ccxt so that
# Freqtrade's `getattr(ccxt, 'max')` and `is_exchange_known_ccxt()` work.
if not hasattr(ccxt, "max"):
    ccxt.max = MaxCcxt
    if "max" not in ccxt.exchanges:
        ccxt.exchanges.append("max")

# Also register in async module (Freqtrade falls back to this)
if not hasattr(ccxt_async, "max"):
    ccxt_async.max = MaxCcxt
    if "max" not in ccxt_async.exchanges:
        ccxt_async.exchanges.append("max")


class Max(Exchange):
    """
    MAX exchange class. Contains adjustments needed for Freqtrade to work
    with this exchange.

    Spot trading only. TWD and USDT pairs supported.
    """

    _ft_has: FtHas = {
        "ohlcv_candle_limit": 1000,
        "ohlcv_has_history": True,
        "trades_has_history": False,
        "tickers_have_quoteVolume": False,
        "tickers_have_percentage": False,
        "tickers_have_bid_ask": True,
        "l2_limit_range": [1, 5, 10, 20, 50, 100],
    }
