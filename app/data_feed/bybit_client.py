"""Bybit data-feed client wrapping REST and WebSocket access.

The client covers the endpoints mentioned in TZ §8.1 and §4.2/4.5:

* ``GET /v5/market/kline`` for multi-timeframe candles;
* ``GET /v5/market/orderbook`` for Level-1/Level-50 snapshots used in
  spread/depth filters (``mid_price``, ``spread_bps``, ``depth_±1%_usd``);
* ``GET /v5/market/recent-trade`` for the trade feed (``volume_5m``,
  ``rel_volume_5m``, ``delta_flow_1m``);
* ``GET /v5/market/open-interest`` for ``oi_delta_5m``;
* ``POST /v5/order/create`` / ``/v5/order/cancel`` / ``/v5/position/list`` as
  the initial trading primitives (orders/positions, TZ §8.1).

Latency is recorded for every REST call as ``(response_time - request_time)`` in
milliseconds; ``latency_ms`` then feeds MarketState filters (§4.5).

The module also exposes a thin WebSocket wrapper for public topics
(``wss://stream(.testnet).bybit.com/v5/public/linear``) so downstream modules can
subscribe to klines/orderbook/trades with <200 ms push latency when required.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Generic, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple, TypeVar

import httpx

from app.config.models import ApiCredentialsConfig, BybitMode, BybitTradingConfig
from app.core.enums import OrderType, Side, TimeInForce
from app.core.types import Symbol

from .candles import Candle, Timeframe, parse_kline_response
from .orderbook import OrderBookSnapshot, parse_orderbook_response
from .trades import Trade, parse_trade_response

try:  # Optional dependency – websocket-client is loaded lazily
    import websocket
except ImportError:  # pragma: no cover - optional path for environments w/o websocket-client
    websocket = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)

DEFAULT_REST_ENDPOINTS: Mapping[BybitMode, str] = {
    BybitMode.LIVE: "https://api.bybit.com",
    BybitMode.DEMO: "https://api-testnet.bybit.com",
}

DEFAULT_WS_PUBLIC_ENDPOINTS: Mapping[BybitMode, str] = {
    BybitMode.LIVE: "wss://stream.bybit.com/v5/public/linear",
    BybitMode.DEMO: "wss://stream-testnet.bybit.com/v5/public/linear",
}

DEFAULT_WS_PRIVATE_ENDPOINTS: Mapping[BybitMode, str] = {
    BybitMode.LIVE: "wss://stream.bybit.com/v5/private",
    BybitMode.DEMO: "wss://stream-testnet.bybit.com/v5/private",
}

RecvWindow = 5000
T = TypeVar("T")


class BybitApiError(RuntimeError):
    """Raised when Bybit returns ``retCode`` != 0."""

    def __init__(self, code: int, message: str, payload: Mapping[str, Any]):
        super().__init__(f"Bybit error {code}: {message}")
        self.code = code
        self.payload = payload


@dataclass(slots=True)
class DataWithLatency(Generic[T]):
    """Container used by fetch helpers to propagate measured latency."""

    data: T
    latency_ms: float


@dataclass(slots=True)
class OpenInterestPoint:
    """Single open interest data-point (timestamp is in milliseconds)."""

    symbol: Symbol
    timestamp_ms: int
    open_interest: float


@dataclass(slots=True)
class OpenInterestStats(OpenInterestPoint):
    """Enriched open interest metrics for MarketState."""

    delta_5m: Optional[float]


class BybitClient:
    """Synchronous REST/WebSocket client for Bybit linear USDT-perps.

    Parameters
    ----------
    trading_config:
        Instance of :class:`app.config.models.BybitTradingConfig` with mode and
        optional custom endpoints.
    credentials:
        :class:`app.config.models.ApiCredentialsConfig` providing demo/live
        API keys.
    session:
        Optional pre-configured :class:`httpx.Client` (e.g. for tests).

    Notes
    -----
    * Retries use exponential backoff ``base_delay * 2 ** attempt`` capped at the
      provided timeout per request. Temporary HTTP/network issues are logged as
      warnings and re-raised after the final attempt.
    * Signing follows Bybit V5 spec: ``sign = SHA256(timestamp + apiKey +
      recvWindow + body)``, where ``body`` is either JSON (POST) or query string
      (GET private endpoints).
    """

    def __init__(
        self,
        trading_config: BybitTradingConfig,
        credentials: ApiCredentialsConfig,
        session: httpx.Client | None = None,
        *,
        timeout: float = 5.0,
        max_retries: int = 3,
        backoff_base: float = 0.25,
    ) -> None:
        self.mode = trading_config.mode
        creds = credentials.bybit.demo if self.mode == BybitMode.DEMO else credentials.bybit.live
        self.api_key = creds.api_key
        self.api_secret = creds.api_secret.encode()
        self._rest_base = trading_config.rest_endpoint or DEFAULT_REST_ENDPOINTS[self.mode]
        self._ws_public_url = trading_config.ws_endpoint or DEFAULT_WS_PUBLIC_ENDPOINTS[self.mode]
        self._ws_private_url = DEFAULT_WS_PRIVATE_ENDPOINTS[self.mode]
        self._client = session or httpx.Client(base_url=self._rest_base, timeout=timeout)
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying HTTP client."""

        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        data: Optional[MutableMapping[str, Any]] = None,
        auth: bool = False,
    ) -> tuple[Mapping[str, Any] | None, float]:
        """Perform an HTTP request with retry/backoff and return JSON payload.

        Returns a tuple ``(result, latency_ms)``. ``result`` is the ``result``
        field from Bybit's JSON response.
        """

        url_path = path if path.startswith("/") else f"/{path}"
        attempt = 0
        last_error: Exception | None = None
        while attempt < self._max_retries:
            start = time.perf_counter()
            try:
                headers: Dict[str, str] = {}
                req_params = dict(params or {})
                body_payload = data.copy() if data else None
                if auth:
                    headers.update(self._build_auth_headers(req_params, body_payload))
                response = self._client.request(
                    method,
                    url_path,
                    params=req_params,
                    json=body_payload,
                    headers=headers,
                )
                latency_ms = (time.perf_counter() - start) * 1_000.0
                response.raise_for_status()
                payload = response.json()
                ret_code = payload.get("retCode", -1)
                if ret_code != 0:
                    raise BybitApiError(ret_code, payload.get("retMsg", ""), payload)
                return payload.get("result"), latency_ms
            except (httpx.HTTPError, BybitApiError) as exc:  # pragma: no cover - network specific path
                last_error = exc
                LOGGER.warning("Bybit %s %s failed (attempt %s/%s): %s", method, path, attempt + 1, self._max_retries, exc)
                time.sleep(self._backoff_base * (2 ** attempt))
                attempt += 1
        assert last_error is not None
        raise last_error

    def _build_auth_headers(
        self,
        params: Mapping[str, Any],
        body: Optional[Mapping[str, Any]],
    ) -> Dict[str, str]:
        """Return Bybit V5 auth headers for REST calls."""

        timestamp = str(int(time.time() * 1000))
        recv_window = str(RecvWindow)
        payload = self._serialize_body(params, body)
        to_sign = f"{timestamp}{self.api_key}{recv_window}{payload}"
        signature = hmac.new(self.api_secret, to_sign.encode(), hashlib.sha256).hexdigest()
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
        }

    @staticmethod
    def _serialize_body(params: Mapping[str, Any], body: Optional[Mapping[str, Any]]) -> str:
        """Serialize parameters/body according to Bybit V5 signing rules."""

        if body:
            return json.dumps(body, separators=(",", ":"), sort_keys=True)
        if not params:
            return ""
        items = sorted(params.items())
        return "&".join(f"{key}={value}" for key, value in items)

    # ------------------------------------------------------------------
    # Public market data
    # ------------------------------------------------------------------
    def fetch_candles(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        limit: int = 200,
    ) -> DataWithLatency[Sequence[Candle]]:
        """Return recent candles for ``symbol`` and timeframe.

        Wrapper over ``GET /v5/market/kline`` with ``category=linear``.
        """

        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": timeframe.value,
            "limit": limit,
        }
        result, latency = self._request("GET", "/v5/market/kline", params=params)
        candles = parse_kline_response(symbol, timeframe, result)
        return DataWithLatency(candles, latency)

    def fetch_orderbook(
        self,
        symbol: Symbol,
        depth: int = 50,
    ) -> DataWithLatency[OrderBookSnapshot]:
        """Return Level-``depth`` order book snapshot for ``symbol``.

        Uses ``GET /v5/market/orderbook`` (linear). ``depth`` must be <= 200 per
        Bybit docs. Result feeds depth/spread filters (§4.5) and liquidity score.
        """

        params = {
            "category": "linear",
            "symbol": symbol,
            "limit": depth,
        }
        result, latency = self._request("GET", "/v5/market/orderbook", params=params)
        snapshot = parse_orderbook_response(symbol, result)
        return DataWithLatency(snapshot, latency)

    def fetch_trades(
        self,
        symbol: Symbol,
        limit: int = 200,
    ) -> DataWithLatency[Sequence[Trade]]:
        """Return the latest trades for ``symbol`` (taker-aggressor feed).

        Calls ``GET /v5/market/recent-trade``. Bybit returns the most recent
        trades in descending timestamp order. ``Trade`` objects record the taker
        side so ``delta_flow_1m`` can be computed downstream.
        """

        params = {
            "category": "linear",
            "symbol": symbol,
            "limit": limit,
        }
        result, latency = self._request("GET", "/v5/market/recent-trade", params=params)
        trades = parse_trade_response(symbol, result)
        return DataWithLatency(trades, latency)

    def fetch_open_interest(
        self,
        symbol: Symbol,
        interval: str = "5min",
    ) -> DataWithLatency[OpenInterestStats]:
        """Return open interest info and the 5-minute delta.

        The Bybit endpoint returns a chronological list where the first element
        is the most recent value. ``delta_5m`` equals ``list[0] - list[1]``
        (i.e. compare the latest point to the immediately previous bucket).
        """

        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": 2,
        }
        result, latency = self._request("GET", "/v5/market/open-interest", params=params)
        stats = self._parse_open_interest(symbol, result)
        return DataWithLatency(stats, latency)

    # ------------------------------------------------------------------
    # Trading primitives (REST private)
    # ------------------------------------------------------------------
    def place_order(
        self,
        *,
        symbol: Symbol,
        side: Side,
        order_type: OrderType,
        qty: float,
        price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        reduce_only: bool = False,
        close_on_trigger: bool = False,
    ) -> Mapping[str, Any]:
        """Place an order via ``POST /v5/order/create``.

        Parameters follow TZ §8.1 – only the subset needed by the strategies is
        exposed right now. Additional fields (trigger price, take-profit) can be
        added without breaking call sites.
        """

        body: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": side.name.capitalize(),
            "orderType": order_type.name.capitalize(),
            "qty": str(qty),
            "timeInForce": time_in_force.name,
            "reduceOnly": reduce_only,
            "closeOnTrigger": close_on_trigger,
        }
        if price is not None:
            body["price"] = str(price)
        result, _ = self._request("POST", "/v5/order/create", data=body, auth=True)
        return result or {}

    def cancel_order(self, *, symbol: Symbol, order_id: str | None = None, client_order_id: str | None = None) -> Mapping[str, Any]:
        """Cancel an order (``POST /v5/order/cancel``)."""

        body: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
        }
        if order_id:
            body["orderId"] = order_id
        if client_order_id:
            body["orderLinkId"] = client_order_id
        result, _ = self._request("POST", "/v5/order/cancel", data=body, auth=True)
        return result or {}

    def list_positions(self, *, symbol: Optional[Symbol] = None) -> Sequence[Mapping[str, Any]]:
        """Fetch current positions via ``GET /v5/position/list``."""

        params: Dict[str, Any] = {
            "category": "linear",
        }
        if symbol:
            params["symbol"] = symbol
        result, _ = self._request("GET", "/v5/position/list", params=params, auth=True)
        if not result:
            return []
        return result.get("list", [])

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------
    def build_public_ws(self, topics: Iterable[str]) -> "BybitWebSocketSession":
        """Instantiate a public WebSocket session subscribing to ``topics``."""

        return BybitWebSocketSession(self._ws_public_url, topics)

    def build_private_ws(self, topics: Iterable[str]) -> "BybitWebSocketSession":
        """Instantiate a private WebSocket session with auth handshake."""

        return BybitWebSocketSession(
            self._ws_private_url,
            topics,
            api_key=self.api_key,
            api_secret=self.api_secret,
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------
    def _parse_open_interest(self, symbol: Symbol, payload: Mapping[str, Any] | None) -> OpenInterestStats:
        if not payload:
            return OpenInterestStats(symbol=symbol, timestamp_ms=0, open_interest=0.0, delta_5m=None)
        entries = payload.get("list", [])
        if not entries:
            return OpenInterestStats(symbol=symbol, timestamp_ms=0, open_interest=0.0, delta_5m=None)
        # Entries arrive newest → oldest per Bybit documentation.
        points: list[OpenInterestPoint] = []
        for item in entries:
            ts = int(item.get("timestamp", 0))
            oi = float(item.get("openInterest", 0.0))
            points.append(OpenInterestPoint(symbol=symbol, timestamp_ms=ts, open_interest=oi))
        points.sort(key=lambda entry: entry.timestamp_ms, reverse=True)
        latest = points[0]
        delta = None
        if len(points) > 1:
            delta = latest.open_interest - points[1].open_interest
        return OpenInterestStats(symbol=latest.symbol, timestamp_ms=latest.timestamp_ms, open_interest=latest.open_interest, delta_5m=delta)


class BybitWebSocketSession:
    """Blocking WebSocket session for Bybit public/private topics.

    The wrapper is intentionally lightweight – MarketState builders can run it in
    a dedicated thread and feed asyncio queues. Authentication for private topics
    mirrors REST signing (timestamp + apiKey + recvWindow + sign payload).
    """

    def __init__(
        self,
        url: str,
        topics: Iterable[str],
        *,
        api_key: Optional[str] = None,
        api_secret: Optional[bytes] = None,
        recv_window: int = RecvWindow,
    ) -> None:
        self._url = url
        self._topics = list(topics)
        self._api_key = api_key
        self._api_secret = api_secret
        self._recv_window = recv_window
        self._socket: "websocket.WebSocket" | None = None

    def __enter__(self) -> "BybitWebSocketSession":  # pragma: no cover - network usage
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - network usage
        self.close()

    def connect(self) -> None:  # pragma: no cover - network usage
        if websocket is None:
            raise RuntimeError("websocket-client is required for WS streaming")
        self._socket = websocket.WebSocket()
        self._socket.connect(self._url)
        if self._api_key and self._api_secret:
            self._authenticate()
        if self._topics:
            self.subscribe(self._topics)

    def close(self) -> None:  # pragma: no cover - network usage
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def subscribe(self, topics: Iterable[str]) -> None:  # pragma: no cover - network usage
        if not self._socket:
            raise RuntimeError("WebSocket is not connected")
        payload = json.dumps({"op": "subscribe", "args": list(topics)})
        self._socket.send(payload)

    def recv(self, timeout: float | None = None) -> Mapping[str, Any]:  # pragma: no cover - network usage
        if not self._socket:
            raise RuntimeError("WebSocket is not connected")
        if timeout is not None:
            self._socket.settimeout(timeout)
        raw = self._socket.recv()
        return json.loads(raw)

    def _authenticate(self) -> None:  # pragma: no cover - network usage
        assert self._api_key is not None and self._api_secret is not None
        timestamp = str(int(time.time() * 1000))
        payload = ""
        to_sign = f"{timestamp}{self._api_key}{self._recv_window}{payload}"
        signature = hmac.new(self._api_secret, to_sign.encode(), hashlib.sha256).hexdigest()
        auth_msg = json.dumps(
            {
                "op": "auth",
                "args": [self._api_key, str(self._recv_window), timestamp, signature],
            }
        )
        assert self._socket is not None
        self._socket.send(auth_msg)
