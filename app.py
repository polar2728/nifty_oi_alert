import streamlit as st
import pandas as pd
from datetime import datetime, time as dtime, timezone, timedelta
from fyers_apiv3 import fyersModel

# ================= CONFIG =================
OI_SPIKE_THRESHOLD = 500
MIN_BASE_OI = 1000
MAX_ALERTS_TO_KEEP = 50
STRIKE_RANGE_POINTS = 150

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# ================= SECRETS =================
client_id = st.secrets["client_id"]
access_token = st.secrets["access_token"]

# ================= FYERS =================
fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")

# ================= SESSION =================
if "prev_oi" not in st.session_state:
    st.session_state.prev_oi = {}
if "alerts" not in st.session_state:
    st.session_state.alerts = []
if "warmed_up" not in st.session_state:
    st.session_state.warmed_up = False

# ================= CACHED API CALLS =================
@st.cache_data(ttl=600, show_spinner="Fetching Nifty spot...")  # 10 minutes
def get_nifty_spot():
    try:
        q = fyers.quotes({"symbols": "NSE:NIFTY50-INDEX"})
        if q.get("s") == "ok" and q.get("d"):
            return round(q["d"][0]["v"]["lp"])
        else:
            st.error(f"Quotes failed: {q}")
            return None
    except Exception as e:
        st.error(f"Quotes error: {e}")
        return None

@st.cache_data(ttl=1800, show_spinner="Loading option chain...")  # 30 minutes
def fetch_option_chain():
    try:
        resp = fyers.optionchain({
            "symbol": "NSE:NIFTY50-INDEX",
            "strikecount": 20,
            "timestamp": ""
        })
        if resp.get("s") != "ok":
            st.error(f"Chain failed: {resp}")
            return None, None
        data = resp["data"]
        return data["optionsChain"], data.get("expiryData", [])
    except Exception as e:
        st.error(f"Chain error: {e}")
        return None, None

# ================= CORE LOGIC (rest remains the same) =================
# ... (keep your existing reset_on_new_trading_day, get_current_weekly_expiry, scan_for_oi_spikes, etc.)

# ================= UI (simplified - no forced loop) =================
spot, atm, new_alerts, expiry = scan_for_oi_spikes()

if spot:
    c1, c2, c3 = st.columns([2, 2, 3])
    c1.metric("NIFTY Spot", f"{spot:,.0f}")
    c2.metric("ATM Strike", f"{atm:,.0f}")
    c3.markdown(f"**Weekly Expiry:** {expiry}")

    # ... your alerts display code ...

    st.success("Last checked: " + datetime.now(IST).strftime("%H:%M:%S"))

else:
    st.warning("Market closed or data unavailable right now")

# Optional manual refresh button (no auto-loop)
if st.button("Refresh Now"):
    st.rerun()