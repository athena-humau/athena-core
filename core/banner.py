from config.settings import *

def show_banner():
    print("=" * 60)
    print(APP_NAME)
    print("=" * 60)
    print(f"Version      : {VERSION}")
    print(f"Environment  : {ENVIRONMENT}")
    print(f"Exchange     : {EXCHANGE}")
    print(f"Market       : {MARKET}")
    print(f"Motto        : {MOTTO}")
    print("=" * 60)