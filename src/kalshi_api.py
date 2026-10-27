import logging
import time
import requests

from config import (
    KALSHI_API_KEY,
    KALSHI_API_BASE_URL,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
)

class KalshiAPI:
    def __init__(
        self,
        api_key=None,
        base_url=None,
        max_retries=MAX_RETRIES,
        retry_delay=RETRY_DELAY_SECONDS,
        transport=None,
    ):
        self.api_key = api_key or KALSHI_API_KEY
        self.base_url = base_url or KALSHI_API_BASE_URL
        self.logger = logging.getLogger(__name__)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        if transport is not None and not callable(transport):
            raise TypeError("transport must be callable")
        self.transport = transport

    def _handle_request(self, method, endpoint, **kwargs):
        # A supplied transport completely owns the request. In particular, a
        # paper transport failure must never fall through to the HTTP client.
        if self.transport is not None:
            return self.transport(method, endpoint, **kwargs)
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        attempt = 0
        backoff = self.retry_delay

        while attempt < self.max_retries:
            try:
                response = requests.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                if response.content:
                    return response.json()
                return {}
            except requests.exceptions.HTTPError as http_err:
                status_code = getattr(http_err.response, "status_code", None)
                if status_code and 400 <= status_code < 500:
                    self.logger.error(
                        f"Non-retriable HTTP error ({status_code}) for {endpoint}: {http_err}"
                    )
                    break
                self.logger.warning(
                    f"HTTP error ({status_code}) on attempt {attempt + 1}/{self.max_retries} "
                    f"for {endpoint}: {http_err}. Retrying in {backoff}s."
                )
            except requests.exceptions.RequestException as req_err:
                self.logger.warning(
                    f"Request exception on attempt {attempt + 1}/{self.max_retries} "
                    f"for {endpoint}: {req_err}. Retrying in {backoff}s."
                )

            attempt += 1
            if attempt < self.max_retries:
                time.sleep(backoff)
                backoff *= 2

        self.logger.error(
            f"Failed to complete request to {endpoint} after {self.max_retries} attempts."
        )
        return None

    # ---- Exchange endpoints ----
    def get_exchange_status(self):
        return self._handle_request("GET", "/exchange/status")

    def get_exchange_announcements(self):
        return self._handle_request("GET", "/exchange/announcements")

    # ---- Market & event data ----
    def get_markets(self, params=None):
        return self._handle_request("GET", "/markets", params=params or {})

    def get_market(self, market_ticker, params=None):
        return self._handle_request(
            "GET", f"/markets/{market_ticker}", params=params or {}
        )

    def get_events(self, params=None):
        return self._handle_request("GET", "/events", params=params or {})

    # ---- Portfolio endpoints ----
    def get_account_balance(self):
        return self._handle_request("GET", "/portfolio/balance")

    def get_positions(self, params=None):
        return self._handle_request("GET", "/portfolio/positions", params=params or {})

    def get_orders(self, params=None):
        return self._handle_request("GET", "/portfolio/orders", params=params or {})

    def create_order(self, order_payload):
        return self._handle_request("POST", "/portfolio/orders", json=order_payload)

    def cancel_order(self, order_id):
        return self._handle_request("DELETE", f"/portfolio/orders/{order_id}")

    # ---- Backwards-compatible helpers ----
    def fetch_market_data(self, params=None):
        """Legacy alias for get_markets used elsewhere in the bot."""
        return self.get_markets(params=params)

    def get_market_data(self, market_id):
        """Legacy alias for get_market to avoid breaking references."""
        return self.get_market(market_id)
