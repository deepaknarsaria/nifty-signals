"""Health check: prints one GitHub annotation per step, never prints secret values."""
import os, platform, sys, traceback
def note(level, msg):
    print(f"::{level}::{msg}".replace("\n", " | ")[:900], flush=True)
note("notice", f"python {sys.version.split()[0]} on {platform.platform()}")
try:
    import angel_data as A
    note("notice", "env present: " + ",".join(k for k in ["ANGEL_API_KEY","ANGEL_CLIENT_CODE","ANGEL_PIN","ANGEL_TOTP_SECRET","TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID"] if os.getenv(k)))
    api = A.Angel(); note("notice", "Angel login OK")
    m = A.scrip_master(); note("notice", f"scrip master rows {len(m)}")
    q = api.quote({"NSE": ["99926000"]}, "LTP"); note("notice", f"NIFTY quote {q[:1]}")
    import datetime as d
    c = api.candles("NSE", "99926000", d.date.today() - d.timedelta(days=5), d.date.today()); note("notice", f"candles {len(c)}")
except Exception as e:
    note("error", f"{type(e).__name__}: {e} || {traceback.format_exc()[-600:]}")
