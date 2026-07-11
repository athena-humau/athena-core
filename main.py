from core.banner import show_banner
from core.startup import startup
from core.app import Athena

show_banner()

startup()

app = Athena()

app.run()