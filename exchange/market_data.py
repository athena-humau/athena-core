import requests


class MarketData:

    BASE_URL = "https://api.bitget.com"

    def get_ticker(self, symbol: str):

        url = f"{self.BASE_URL}/api/v2/mix/market/ticker"

        params = {
            "symbol": symbol,
            "productType": "USDT-FUTURES"
        }

        response = requests.get(url, params=params, timeout=10)

        return response.json()

    def get_symbols(self):

        url = f"{self.BASE_URL}/api/v2/mix/market/contracts"

        params = {
            "productType": "USDT-FUTURES"
        }

        response = requests.get(url, params=params, timeout=10)

        return response.json()