import streamlit as st
import pandas as pd
import time
from datetime import datetime, time as dtime, timezone, timedelta
from fyers_apiv3 import fyersModel

# ================= CONFIG =================
REFRESH_INTERVAL     = 300
OI_SPIKE_THRESHOLD   = 500
MIN_BASE_OI          = 1000
MAX_ALERTS_TO_KEEP   = 50
MARKET_OPEN_TIME     = dtime(9, 15)
MARKET_CLOSE_TIME    = dtime(15, 30)
STRIKE_RANGE_POINTS  = 150

# ================= STREAMLIT =================
st.set_page_config(page_title="Nifty OI Alert", layout="wide")
st.title("🔥 NIFTY Weekly OI Spike Alert (ATM ±150)")
st.caption("Alerts when OI increases >500% on current weekly expiry strikes")

# ================= SECRETS =================
client_id = st.secrets["client_id"]
access_token = st.secrets["access_token"]

# ================= FYERS =================
fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")

# ================= SESSION =================
for key in ["prev_oi", "alerts", "last_check", "warmed_up", "last_run_date"]:
    if key not in st.session_state:
        if key == "prev_oi":
            st.session_state[key] = {}
        elif key == "alerts":
            st.session_state[key] = []
        elif key == "warmed_up":
            st.session_state[key] = False
        else:
            st.session_state[key] = None

# ================= HELPERS =================
IST_OFFSET = timedelta(hours=5, minutes=30)
IST = timezone(IST_OFFSET)

def is_market_open():
    now_ist = datetime.now(IST).time()
    return True
    # return dtime(9, 15) <= now_ist <= dtime(15, 30)


def reset_on_new_trading_day():
    today = datetime.now().date()
    if st.session_state.last_run_date != today and is_market_open():
        st.session_state.prev_oi = {}
        st.session_state.warmed_up = False
        st.session_state.alerts = []
        st.session_state.last_run_date = today
        st.toast("New trading day → OI baseline reset", icon="🔄")


def get_nifty_spot():
    try:
        q = fyers.quotes({"symbols": "NSE:NIFTY50-INDEX"})
        st.write("Raw quotes response:", q)  # This will show in app/logs
        return round(q["d"][0]["v"]["lp"])
    except Exception as e:
        st.error(f"Quotes error: {e}")
        return None

def fetch_option_chain():
    try:
        resp = fyers.optionchain({
            "symbol": "NSE:NIFTY50-INDEX",
            "strikecount": 20,
            "timestamp": ""
        })
        if resp.get("s") != "ok":
            return None, None
        data = resp["data"]
        return data["optionsChain"], data.get("expiryData", [])
    except:
        return None, None


def get_current_weekly_expiry(expiry_list):
    if not expiry_list:
        return "Unknown"
    today = datetime.now().date()
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
    return expiry_list[0]["date"]


# ================= CORE =================
def scan_for_oi_spikes():
    if not is_market_open():
        return None, None, [], "Market Closed"

    reset_on_new_trading_day()

    spot = get_nifty_spot()
    st.write(f"Debug: Spot = {spot}")
    if not spot:
        return None, None, [], "Spot Error"

    atm = int(round(spot / 50) * 50)

    raw, expiry_info = fetch_option_chain()
    if raw is None:
        return spot, atm, [], "API Error"

    df = pd.DataFrame(raw)
    expiry = get_current_weekly_expiry(expiry_info)

    # Filter current weekly expiry
    df = df[df["symbol"].str.contains(expiry, regex=False, na=False)]

    # ATM range
    df = df[
        (df["strike_price"] >= atm - STRIKE_RANGE_POINTS) &
        (df["strike_price"] <= atm + STRIKE_RANGE_POINTS)
    ]

    if df.empty:
        return spot, atm, [], f"{expiry} (no strikes found)"

    current_oi = {}
    new_alerts = []

    for _, row in df.iterrows():
        strike = int(row["strike_price"])
        opt_type = row["option_type"]
        oi = int(row.get("oi", 0))
        key = f"{opt_type}_{strike}"

        current_oi[key] = oi

        prev = st.session_state.prev_oi.get(key, 0)

        if (st.session_state.warmed_up and 
            prev >= MIN_BASE_OI and 
            oi > prev):
            pct = (oi - prev) / prev * 100
            if pct > OI_SPIKE_THRESHOLD:
                new_alerts.append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "msg": f"{opt_type} {strike} | +{pct:.1f}% | {prev:,} → {oi:,}"
                })

    if not st.session_state.warmed_up:
        st.session_state.prev_oi = current_oi
        st.session_state.warmed_up = True
        st.session_state.last_check = datetime.now().strftime("%H:%M:%S")
        return spot, atm, [], expiry

    st.session_state.prev_oi = current_oi
    st.session_state.last_check = datetime.now().strftime("%H:%M:%S")

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

    st.success(f"Last checked: {st.session_state.last_check}  |  Refresh every 5 min")
else:
    st.warning("Market closed or data unavailable right now")

time.sleep(REFRESH_INTERVAL)
st.rerun()