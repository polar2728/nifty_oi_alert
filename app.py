import streamlit as st
import pandas as pd
from datetime import datetime, time as dtime, timedelta, timezone
from fyers_apiv3 import fyersModel

# ================= PAGE CONFIG =================
st.set_page_config(
    page_title="NIFTY OI Spike Monitor",
    layout="wide"
)

st.title("📊 NIFTY Weekly OI Spike Monitor")
st.caption("Manual scan • Streamlit Cloud compatible")

# ================= CONFIG =================
OI_SPIKE_THRESHOLD  = 500
MIN_BASE_OI         = 1000
STRIKE_RANGE_POINTS = 100

# ================= TIMEZONE =================
IST = timezone(timedelta(hours=5, minutes=30))

# ================= SESSION STATE =================
if "prev_oi" not in st.session_state:
    st.session_state.prev_oi = {}
    st.session_state.prev_ltp = {}
    st.session_state.warmed_up = False
    st.session_state.last_run_date = None

# ================= SECRETS =================
CLIENT_ID    = st.secrets["client_id"]
ACCESS_TOKEN = st.secrets["access_token"]

# ================= FYERS =================
fyers = fyersModel.FyersModel(
    client_id=CLIENT_ID,
    token=ACCESS_TOKEN,
    log_path=""
)

# ================= HELPERS =================
def now_ist():
    return datetime.now(IST)

def is_market_open():
    t = now_ist().time()
    return True
    # return dtime(9, 15) <= t <= dtime(15, 30)

def reset_on_new_trading_day():
    today = now_ist().date()
    if st.session_state.last_run_date != today and is_market_open():
        st.session_state.prev_oi = {}
        st.session_state.prev_ltp = {}
        st.session_state.warmed_up = False
        st.session_state.last_run_date = today
        st.info("🔄 New trading day → baseline reset")

# ================= API CALLS =================
def get_nifty_spot():
    q = fyers.quotes({"symbols": "NSE:NIFTY50-INDEX"})
    if q.get("s") == "ok" and q.get("d"):
        return round(q["d"][0]["v"]["lp"])
    return None

def fetch_option_chain():
    resp = fyers.optionchain({
        "symbol": "NSE:NIFTY50-INDEX",
        "strikecount": 40,
        "timestamp": ""
    })
    if resp.get("s") != "ok":
        return None, None
    data = resp["data"]
    return data.get("optionsChain", []), data.get("expiryData", [])

def expiry_to_symbol_format(date_str):
    try:
        d = datetime.strptime(date_str, "%d-%m-%Y")
        return d.strftime("%y") + str(d.month) + d.strftime("%d")
    except:
        return None

def get_current_weekly_expiry(expiry_list):
    today = now_ist().date()
    candidates = []
    for exp in expiry_list:
        try:
            exp_date = datetime.fromtimestamp(int(exp["expiry"])).date()
            candidates.append(((exp_date - today).days, exp["date"]))
        except:
            pass
    return sorted(candidates)[0][1] if candidates else None

# ================= SCAN =================
def scan():
    if not is_market_open():
        st.warning("Market is closed")
        return

    reset_on_new_trading_day()

    spot = get_nifty_spot()
    if spot is None:
        st.error("Failed to fetch NIFTY spot")
        return

    atm = int(round(spot / 50) * 50)

    col1, col2 = st.columns(2)
    col1.metric("NIFTY Spot", f"{spot:,}")
    col2.metric("ATM Strike", atm)

    raw, expiry_info = fetch_option_chain()
    if not raw:
        st.error("Option chain unavailable")
        return

    expiry = get_current_weekly_expiry(expiry_info)
    expiry_filter = expiry_to_symbol_format(expiry) or expiry

    df = pd.DataFrame(raw)
    df = df[df["symbol"].str.contains(expiry_filter, regex=False, na=False)]

    if df.empty:
        st.warning("Expiry filter failed — using all strikes")
        df = pd.DataFrame(raw)

    df = df[
        (df["strike_price"] >= atm - STRIKE_RANGE_POINTS) &
        (df["strike_price"] <= atm + STRIKE_RANGE_POINTS)
    ]

    if df.empty:
        st.warning("No strikes in range")
        return

    alerts = []
    table_rows = []
    current_oi = {}
    current_ltp = {}

    for _, r in df.iterrows():
        strike = int(r.strike_price)
        opt_type = r.option_type
        oi = int(r.oi)
        ltp = float(r.ltp)
        key = f"{opt_type}_{strike}"

        prev_oi_val = st.session_state.prev_oi.get(key, 0)
        oi_pct = ((oi - prev_oi_val) / prev_oi_val * 100) if prev_oi_val > 0 else 0

        table_rows.append([
            opt_type,
            strike,
            oi,
            prev_oi_val if prev_oi_val else None,
            round(oi_pct, 1),
            round(ltp, 2)
        ])

        if (
            st.session_state.warmed_up
            and prev_oi_val >= MIN_BASE_OI
            and oi_pct > OI_SPIKE_THRESHOLD
            and oi > prev_oi_val
        ):
            alerts.append([opt_type, strike, prev_oi_val, oi, round(oi_pct, 1)])

        current_oi[key] = oi
        current_ltp[key] = ltp

    df_view = pd.DataFrame(
        table_rows,
        columns=["Type", "Strike", "OI Now", "OI Prev", "OI %", "LTP"]
    )

    st.subheader(f"Monitoring {len(df_view)} strikes (Expiry: {expiry})")
    st.dataframe(df_view, use_container_width=True, width="stretch")

    if alerts:
        st.subheader("🚨 OI Spike Alerts")
        st.dataframe(
            pd.DataFrame(
                alerts,
                columns=["Type", "Strike", "OI Prev", "OI Now", "OI %"]
            ),
            use_container_width=True,
            width="stretch"
        )
    else:
        st.success("No OI spikes detected")

    if not st.session_state.warmed_up:
        st.session_state.warmed_up = True
        st.info("Baseline captured. Click again to detect spikes.")

    st.session_state.prev_oi = current_oi
    st.session_state.prev_ltp = current_ltp

# ================= UI =================
st.markdown("---")
if st.button("▶ Run OI Scan"):
    scan()

st.caption("No auto-refresh • No WebSocket • Streamlit Cloud safe")
