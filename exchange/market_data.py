import requests


class MarketData:

    BASE_URL = "https://api.bitget.com"
    TIMEOUT = 10

    def _get(self, endpoint, params):

        url = f"{self.BASE_URL}{endpoint}"

        try:
            response = requests.get(
                url,
                params=params,
                timeout=self.TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            if data.get("code") != "00000":
                raise RuntimeError(
                    f"Bitget API error: "
                    f"code={data.get('code')} "
                    f"msg={data.get('msg')}"
                )

            return data

        except requests.RequestException as error:
            raise RuntimeError(
                f"Bitget HTTP request failed: {error}"
            ) from error

        except ValueError as error:
            raise RuntimeError(
                "Bitget returned invalid JSON"
            ) from error

    def get_ticker(self, symbol: str):

        return self._get(
            "/api/v2/mix/market/ticker",
            {
                "symbol": symbol,
                "productType": "USDT-FUTURES"
            }
        )

    def get_symbols(self):

        return self._get(
            "/api/v2/mix/market/contracts",
            {
                "productType": "USDT-FUTURES"
            }
        )

    def get_all_tickers(self):

        return self._get(
            "/api/v2/mix/market/tickers",
            {
                "productType": "USDT-FUTURES"
            }
        )

    def get_multiple_tickers(self, symbols):

        tickers = []

        for symbol in symbols:

            try:
                tickers.append(self.get_ticker(symbol))

            except RuntimeError as error:
                print(f"Error fetching {symbol}: {error}")

        return tickers