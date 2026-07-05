import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BITGET_API_KEY = os.getenv("BITGET_API_KEY")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE")

TRADING_MODE = os.getenv("TRADING_MODE", "SIMULATION")