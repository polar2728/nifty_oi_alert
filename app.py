import streamlit as st
import pandas as pd
import time
from datetime import datetime, time as dtime, timezone, timedelta
from fyers_apiv3 import fyersModel
import threading

# ================= CONFIG =================
OI_SPIKE_THRESHOLD   = 500
MIN_BASE_OI          = 1000
MAX_ALERTS_TO_KEEP   = 50
STRIKE_RANGE_POINTS  = 150

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# Prevent concurrent API calls during burst
api_lock = threading.Lock()

# ================= STREAMLIT =================
st.set_page_config(page_title="Nifty OI Alert", layout="wide")
st.title("🔥 NIFTY Weekly OI Spike Alert (ATM ±150)")
st.caption("Alerts when OI increases >500% on current weekly expiry strikes")

# ================= SECRETS =================
client_id    = st.secrets["client_id"]
access_token = st.secrets["access_token"]

# ================= FYERS =================
fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")

# ================= SESSION STATE =================
if "prev_oi" not in st.session_state:
    st.session_state.prev_oi = {}
if "alerts" not in st.session_state:
    st.session_state.alerts = []
if "warmed_up" not in st.session_state:
    st.session_state.warmed_up = False
if "last_check" not in st.session_state:
    st.session_state.last_check = None
if "last_run_date" not in st.session_state:
    st.session_state.last_run_date = None

# ================= HELPERS =================
def is_market_open():
    now_ist = datetime.now(IST).time()
    return dtime(9, 15) <= now_ist <= dtime(15, 30)

def reset_on_new_trading_day():
    today = datetime.now(IST).date()
    if st.session_state.last_run_date != today and is_market_open():
        st.session_state.prev_oi = {}
        st.session_state.warmed_up = False
        st.session_state.alerts = []
        st.session_state.last_run_date = today
        st.toast("New trading day → OI baseline reset", icon="🔄")

@st.cache_data(ttl=900, show_spinner="Fetching Nifty spot…")  # 15 min
def get_nifty_spot():
    with api_lock:
        time.sleep(0.6)  # tiny delay to avoid burst
        try:
            q = fyers.quotes({"symbols": "NSE:NIFTY50-INDEX"})
            if q.get("s") == "ok" and q.get("d"):
                return round(q["d"][0]["v"]["lp"])
            else:
                st.error(f"Quotes API returned: {q}")
                return None
        except Exception as e:
            st.error(f"Quotes call failed: {str(e)}")
            return None

@st.cache_data(ttl=3600, show_spinner="Loading option chain…")  # 1 hour
def fetch_option_chain():
    with api_lock:
        time.sleep(0.6)
        try:
            resp = fyers.optionchain({
                "symbol": "NSE:NIFTY50-INDEX",
                "strikecount": 20,
                "timestamp": ""
            })
            if resp.get("s") != "ok":
                st.error(f"Option chain returned: {resp}")
                return None, None
            data = resp["data"]
            return data.get("optionsChain", []), data.get("expiryData", [])
        except Exception as e:
            st.error(f"Option chain call failed: {str(e)}")
            return None, None

def get_current_weekly_expiry(expiry_list):
    if not expiry_list:
        return "Unknown"
    today = datetime.now(IST).date()
    weekly = []
    for exp in expiry_list:
        try:
            exp_date = datetime.fromtimestamp(exp["expiry"]).date()
            days = (exp_date - today).days
            if 0 <= days <= 7:
                weekly.append((days, exp["date"]))
        except:
            continue
    if weekly:
        weekly.sort()
        return weekly[0][1]
    return expiry_list[0]["date"] if expiry_list else "Unknown"

# ================= CORE =================
def scan_for_oi_spikes():
    reset_on_new_trading_day()

    spot = get_nifty_spot()
    if spot is None:
        return None, None, [], "Spot fetch failed"

    atm = int(round(spot / 50) * 50)

    raw, expiry_info = fetch_option_chain()
    if raw is None:
        return spot, atm, [], "Chain fetch failed"

    if not raw:
        return spot, atm, [], "Empty chain data"

    df = pd.DataFrame(raw)
    if df.empty:
        return spot, atm, [], "No strikes in chain"

    expiry = get_current_weekly_expiry(expiry_info)

    df = df[df["symbol"].str.contains(expiry, regex=False, na=False)]

    df = df[
        (df["strike_price"] >= atm - STRIKE_RANGE_POINTS) &
        (df["strike_price"] <= atm + STRIKE_RANGE_POINTS)
    ]

    if df.empty:
        return spot, atm, [], f"{expiry} (no matching strikes)"

    current_oi = {}
    new_alerts = []

    for _, row in df.iterrows():
        strike = int(row.get("strike_price", 0))
        opt_type = row.get("option_type", "?")
        oi = int(row.get("oi", 0))
        key = f"{opt_type}_{strike}"

        current_oi[key] = oi

        prev = st.session_state.prev_oi.get(key, 0)

        if st.session_state.warmed_up and prev >= MIN_BASE_OI and oi > prev:
            pct = (oi - prev) / prev * 100
            if pct > OI_SPIKE_THRESHOLD:
                new_alerts.append({
                    "time": datetime.now(IST).strftime("%H:%M:%S"),
                    "msg": f"{opt_type} {strike} | +{pct:.1f}% | {prev:,} → {oi:,}"
                })

    if not st.session_state.warmed_up:
        st.session_state.prev_oi = current_oi
        st.session_state.warmed_up = True
        st.session_state.last_check = datetime.now(IST).strftime("%H:%M:%S")
        return spot, atm, [], expiry

    st.session_state.prev_oi = current_oi
    st.session_state.last_check = datetime.now(IST).strftime("%H:%M:%S")

    return spot, atm, new_alerts, expiry

# ================= UI =================
spot, atm, new_alerts, expiry = scan_for_oi_spikes()

if spot:
    c1, c2, c3 = st.columns([2, 2, 3])
    c1.metric("NIFTY Spot", f"{spot:,.0f}")
    c2.metric("ATM Strike", f"{atm:,.0f}")
    c3.markdown(f"**Weekly Expiry:** {expiry}")

    for a in new_alerts:
        if a["msg"] not in [x["msg"] for x in st.session_state.alerts]:
            st.session_state.alerts.append(a)
            st.toast(f"🚨 {a['msg']}", icon="🚨")

    st.session_state.alerts = st.session_state.alerts[-MAX_ALERTS_TO_KEEP:]

    if st.session_state.alerts:
        st.subheader("🚨 Recent OI Spike Alerts")
        for a in st.session_state.alerts[::-1]:
            st.markdown(f"**{a['time']}**  {a['msg']}")
    else:
        st.info("No significant OI spikes (>500%) detected yet")

    st.success(f"Last checked: {st.session_state.last_check or '—'}")

else:
    st.warning("Market closed or data unavailable right now")

# Only manual refresh
if st.button("Refresh Data Now"):
    st.rerun()