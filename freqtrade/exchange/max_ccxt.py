"""Custom ccxt exchange class for MAX (MaiCoin Assets eXchange).

MAX is a Taiwanese cryptocurrency exchange (https://max.maicoin.com).
Since ccxt does not include MAX natively, this module provides a ccxt-compatible
implementation that can be registered into ccxt at runtime.

API Reference: https://max-api.maicoin.com/doc/v3.html
"""

import hashlib
import hmac
import json
import time
from base64 import b64encode
from typing import Any

import ccxt


class max(ccxt.Exchange):
    """ccxt-compatible exchange class for MAX (MaiCoin Assets eXchange)."""

    def describe(self):
        return self.deep_extend(
            super().describe(),
            {
                "id": "max",
                "name": "MAX",
                "countries": ["TW"],
                "rateLimit": 100,
                "version": "v3",
                "has": {
                    "CORS": None,
                    # Public
                    "fetchMarkets": True,
                    "fetchTicker": True,
                    "fetchTickers": True,
                    "fetchOrderBook": True,
                    "fetchOHLCV": True,
                    "fetchTrades": True,
                    "fetchCurrencies": True,
                    "fetchTime": True,
                    # Private
                    "createOrder": True,
                    "cancelOrder": True,
                    "cancelAllOrders": True,
                    "fetchOrder": True,
                    "fetchOpenOrders": True,
                    "fetchClosedOrders": True,
                    "fetchBalance": True,
                    "fetchMyTrades": True,
                    # Not supported
                    "fetchFundingRate": False,
                    "fetchPositions": False,
                    "setLeverage": False,
                    "setMarginMode": False,
                },
                "timeframes": {
                    "1m": 1,
                    "5m": 5,
                    "15m": 15,
                    "30m": 30,
                    "1h": 60,
                    "2h": 120,
                    "4h": 240,
                    "6h": 360,
                    "12h": 720,
                    "1d": 1440,
                },
                "urls": {
                    "logo": "https://max.maicoin.com/favicon.ico",
                    "api": {
                        "public": "https://max-api.maicoin.com",
                        "private": "https://max-api.maicoin.com",
                    },
                    "www": "https://max.maicoin.com",
                    "doc": [
                        "https://max-api.maicoin.com/doc/v3.html",
                    ],
                },
                "api": {
                    "public": {
                        "get": [
                            "api/v3/markets",
                            "api/v3/currencies",
                            "api/v3/tickers",
                            "api/v3/ticker",
                            "api/v3/depth",
                            "api/v3/trades",
                            "api/v3/k",
                            "api/v3/timestamp",
                        ],
                    },
                    "private": {
                        "get": [
                            "api/v3/info",
                            "api/v3/wallet/spot/accounts",
                            "api/v3/wallet/spot/orders/open",
                            "api/v3/wallet/spot/orders/closed",
                            "api/v3/wallet/spot/orders/history",
                            "api/v3/wallet/spot/trades",
                            "api/v3/order",
                            "api/v3/order/trades",
                        ],
                        "post": [
                            "api/v3/wallet/spot/order",
                        ],
                        "delete": [
                            "api/v3/order",
                            "api/v3/wallet/spot/orders",
                        ],
                    },
                },
                "fees": {
                    "trading": {
                        "tierBased": True,
                        "percentage": True,
                        "maker": 0.001,  # 0.1%
                        "taker": 0.0015,  # 0.15%
                    },
                },
                "requiredCredentials": {
                    "apiKey": True,
                    "secret": True,
                },
                "precisionMode": ccxt.TICK_SIZE,
            },
        )

    def sign(self, path, api="public", method="GET", params={}, headers=None, body=None):
        url = self.urls["api"][api] + "/" + path

        if api == "public":
            if params:
                url += "?" + self.urlencode(params)
        else:
            self.check_required_credentials()
            nonce = str(int(time.time() * 1000))

            # Build payload
            payload_data = {
                "nonce": nonce,
                "path": "/" + path,
            }

            if method == "GET":
                payload_data.update(params)
                if params:
                    url += "?" + self.urlencode(params)
            else:
                payload_data.update(params)
                body = self.json(params) if params else "{}"

            # Encode and sign
            payload_str = self.json(payload_data)
            payload_b64 = b64encode(payload_str.encode("utf-8")).decode("utf-8")
            signature = hmac.new(
                self.secret.encode("utf-8"),
                payload_b64.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            headers = {
                "X-MAX-ACCESSKEY": self.apiKey,
                "X-MAX-PAYLOAD": payload_b64,
                "X-MAX-SIGNATURE": signature,
                "Content-Type": "application/json",
            }

        return {"url": url, "method": method, "body": body, "headers": headers}

    # ──────────────────── Public API ────────────────────

    def fetch_markets(self, params={}):
        response = self.publicGetApiV3Markets(params)
        result = []
        for market in response:
            market_id = market["id"]
            base_id = market["base_unit"]
            quote_id = market["quote_unit"]
            base = self.safe_currency_code(base_id.upper())
            quote = self.safe_currency_code(quote_id.upper())
            symbol = base + "/" + quote
            status = market.get("status", "active")

            price_precision = self.safe_number(market, "quote_unit_precision", 2)
            amount_precision = self.safe_number(market, "base_unit_precision", 4)
            min_amount = self.safe_number(market, "min_base_amount", 0)
            min_cost = self.safe_number(market, "min_quote_amount", 0)

            result.append({
                "id": market_id,
                "symbol": symbol,
                "base": base,
                "quote": quote,
                "baseId": base_id,
                "quoteId": quote_id,
                "active": status == "active",
                "type": "spot",
                "spot": True,
                "margin": False,
                "swap": False,
                "future": False,
                "option": False,
                "contract": False,
                "precision": {
                    "amount": amount_precision,
                    "price": price_precision,
                },
                "limits": {
                    "amount": {"min": min_amount, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": min_cost, "max": None},
                },
                "info": market,
            })
        return result

    def fetch_ticker(self, symbol, params={}):
        self.load_markets()
        market = self.market(symbol)
        request = {"market": market["id"]}
        response = self.publicGetApiV3Ticker(self.extend(request, params))
        return self._parse_ticker(response, market)

    def fetch_tickers(self, symbols=None, params={}):
        self.load_markets()
        response = self.publicGetApiV3Tickers(params)
        result = {}
        for ticker_data in response:
            market_id = ticker_data.get("market")
            if market_id and market_id in self.markets_by_id:
                market = self.markets_by_id[market_id]
                ticker = self._parse_ticker(ticker_data, market)
                result[ticker["symbol"]] = ticker
        return self.filter_by_array(result, "symbol", symbols)

    def _parse_ticker(self, ticker, market=None):
        timestamp = self.safe_timestamp(ticker, "at")
        return {
            "symbol": market["symbol"] if market else None,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "high": self.safe_number(ticker, "high"),
            "low": self.safe_number(ticker, "low"),
            "bid": self.safe_number(ticker, "buy"),
            "bidVolume": None,
            "ask": self.safe_number(ticker, "sell"),
            "askVolume": None,
            "vwap": None,
            "open": self.safe_number(ticker, "open"),
            "close": self.safe_number(ticker, "last"),
            "last": self.safe_number(ticker, "last"),
            "previousClose": None,
            "change": None,
            "percentage": None,
            "average": None,
            "baseVolume": self.safe_number(ticker, "vol"),
            "quoteVolume": self.safe_number(ticker, "vol_in_btc"),
            "info": ticker,
        }

    def fetch_order_book(self, symbol, limit=None, params={}):
        self.load_markets()
        market = self.market(symbol)
        request = {"market": market["id"]}
        if limit is not None:
            request["limit"] = limit
        response = self.publicGetApiV3Depth(self.extend(request, params))
        timestamp = self.safe_timestamp(response, "timestamp")
        return self.parse_order_book(response, symbol, timestamp, "bids", "asks")

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params={}):
        self.load_markets()
        market = self.market(symbol)
        request = {
            "market": market["id"],
            "period": self.safe_integer(self.timeframes, timeframe, 1),
        }
        if since is not None:
            request["timestamp"] = int(since / 1000)
        if limit is not None:
            request["limit"] = limit
        response = self.publicGetApiV3K(self.extend(request, params))
        return self.parse_ohlcvs(response, market, timeframe, since, limit)

    def parse_ohlcv(self, ohlcv, market=None):
        # MAX returns: [timestamp, open, high, low, close, volume]
        return [
            self.safe_timestamp(ohlcv, 0),  # timestamp in seconds -> ms
            self.safe_number(ohlcv, 1),     # open
            self.safe_number(ohlcv, 2),     # high
            self.safe_number(ohlcv, 3),     # low
            self.safe_number(ohlcv, 4),     # close
            self.safe_number(ohlcv, 5),     # volume
        ]

    def fetch_trades(self, symbol, since=None, limit=None, params={}):
        self.load_markets()
        market = self.market(symbol)
        request = {"market": market["id"]}
        if since is not None:
            request["timestamp"] = int(since / 1000)
        if limit is not None:
            request["limit"] = limit
        response = self.publicGetApiV3Trades(self.extend(request, params))
        return self.parse_trades(response, market, since, limit)

    def parse_trade(self, trade, market=None):
        timestamp = self.safe_timestamp(trade, "created_at")
        market_id = self.safe_string(trade, "market")
        market = self.safe_market(market_id, market)
        price = self.safe_number(trade, "price")
        amount = self.safe_number(trade, "volume")
        cost = self.safe_number(trade, "funds")
        side = self.safe_string(trade, "side")
        trade_id = self.safe_string(trade, "id")
        order_id = self.safe_string(trade, "order_id")
        fee_cost = self.safe_number(trade, "fee")
        fee_currency = self.safe_string(trade, "fee_currency")

        return {
            "id": trade_id,
            "info": trade,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "symbol": market["symbol"],
            "order": order_id,
            "type": None,
            "side": side,
            "takerOrMaker": None,
            "price": price,
            "amount": amount,
            "cost": cost if cost else (price * amount if price and amount else None),
            "fee": {
                "cost": fee_cost,
                "currency": self.safe_currency_code(fee_currency.upper()) if fee_currency else None,
            } if fee_cost is not None else None,
        }

    # ──────────────────── Private API ────────────────────

    def fetch_balance(self, params={}):
        self.load_markets()
        response = self.privateGetApiV3WalletSpotAccounts(params)
        result = {"info": response}
        for balance in response:
            currency_id = self.safe_string(balance, "currency")
            code = self.safe_currency_code(currency_id.upper())
            account = self.account()
            account["free"] = self.safe_string(balance, "balance")
            account["used"] = self.safe_string(balance, "locked")
            result[code] = account
        return self.safe_balance(result)

    def create_order(self, symbol, type, side, amount, price=None, params={}):
        self.load_markets()
        market = self.market(symbol)
        request = {
            "market": market["id"],
            "side": side,
            "volume": str(amount),
            "ord_type": type,
        }
        if price is not None:
            request["price"] = str(price)
        response = self.privatePostApiV3WalletSpotOrder(self.extend(request, params))
        return self._parse_order(response, market)

    def cancel_order(self, id, symbol=None, params={}):
        request = {"id": int(id)}
        response = self.privateDeleteApiV3Order(self.extend(request, params))
        return self._parse_order(response)

    def fetch_order(self, id, symbol=None, params={}):
        request = {"id": int(id)}
        response = self.privateGetApiV3Order(self.extend(request, params))
        return self._parse_order(response)

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params={}):
        self.load_markets()
        request = {}
        market = None
        if symbol is not None:
            market = self.market(symbol)
            request["market"] = market["id"]
        if limit is not None:
            request["limit"] = limit
        response = self.privateGetApiV3WalletSpotOrdersOpen(self.extend(request, params))
        return self.parse_orders(response, market, since, limit)

    def fetch_closed_orders(self, symbol=None, since=None, limit=None, params={}):
        self.load_markets()
        request = {}
        market = None
        if symbol is not None:
            market = self.market(symbol)
            request["market"] = market["id"]
        if limit is not None:
            request["limit"] = limit
        response = self.privateGetApiV3WalletSpotOrdersClosed(self.extend(request, params))
        return self.parse_orders(response, market, since, limit)

    def fetch_my_trades(self, symbol=None, since=None, limit=None, params={}):
        self.load_markets()
        request = {}
        market = None
        if symbol is not None:
            market = self.market(symbol)
            request["market"] = market["id"]
        if since is not None:
            request["timestamp"] = int(since / 1000)
        if limit is not None:
            request["limit"] = limit
        response = self.privateGetApiV3WalletSpotTrades(self.extend(request, params))
        return self.parse_trades(response, market, since, limit)

    def _parse_order(self, order, market=None):
        market_id = self.safe_string(order, "market")
        market = self.safe_market(market_id, market)
        timestamp = self.safe_timestamp(order, "created_at")

        status_map = {
            "wait": "open",
            "convert": "open",
            "done": "closed",
            "cancel": "canceled",
            "finalizing": "open",
        }
        raw_status = self.safe_string(order, "state")
        status = status_map.get(raw_status, raw_status)

        return {
            "id": self.safe_string(order, "id"),
            "clientOrderId": self.safe_string(order, "client_oid"),
            "info": order,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "lastTradeTimestamp": None,
            "symbol": market["symbol"],
            "type": self.safe_string(order, "ord_type"),
            "side": self.safe_string(order, "side"),
            "price": self.safe_number(order, "price"),
            "amount": self.safe_number(order, "volume"),
            "cost": None,
            "average": self.safe_number(order, "avg_price"),
            "filled": self.safe_number(order, "executed_volume"),
            "remaining": self.safe_number(order, "remaining_volume"),
            "status": status,
            "fee": None,
            "trades": None,
        }

    def parse_order(self, order, market=None):
        return self._parse_order(order, market)
