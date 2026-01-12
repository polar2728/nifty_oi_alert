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

# ================= SIDEBAR CONTROLS =================
st.sidebar.header("⚙ Controls")

CHECK_MARKET_HOURS = st.sidebar.toggle(
    "Enable Market Hours Filter (9:15–15:30 IST)",
    value=False
)

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
    return dtime(9, 15) <= t <= dtime(15, 30)

def reset_on_new_trading_day():
    today = now_ist().date()
    if st.session_state.last_run_date != today:
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

# ================= TABLE STYLING =================
def style_table(df):
    def highlight_row(r):
        styles = [""] * len(r)

        if r["ATM"]:
            styles = ["background-color:#fff3cd"] * len(r)

        if abs(r["CE OI %"]) > OI_SPIKE_THRESHOLD:
            styles[0] = "background-color:#ffebee;color:#c62828"

        if abs(r["PE OI %"]) > OI_SPIKE_THRESHOLD:
            styles[-1] = "background-color:#ffebee;color:#c62828"

        if r["CE OI %"] > r["PE OI %"]:
            styles[0] += ";font-weight:600;color:#2e7d32"
        elif r["PE OI %"] > r["CE OI %"]:
            styles[-1] += ";font-weight:600;color:#2e7d32"

        return styles

    return (
        df.style
        .apply(highlight_row, axis=1)
        .format({
            "CE OI %": "{:+.1f}%",
            "PE OI %": "{:+.1f}%",
            "CE LTP": "₹{:.2f}",
            "PE LTP": "₹{:.2f}"
        })
    )

# ================= SCAN =================
def scan():
    if CHECK_MARKET_HOURS and not is_market_open():
        st.warning("⏱ Market is closed (filter enabled)")
        return

    reset_on_new_trading_day()

    spot = get_nifty_spot()
    if spot is None:
        st.error("Failed to fetch NIFTY spot")
        return

    atm = int(round(spot / 50) * 50)

    c1, c2 = st.columns(2)
    c1.metric("NIFTY Spot", f"{spot:,}")
    c2.metric("ATM Strike", atm)

    raw, expiry_info = fetch_option_chain()
    if not raw:
        st.error("Option chain unavailable")
        return

    expiry = get_current_weekly_expiry(expiry_info)
    expiry_filter = expiry_to_symbol_format(expiry) or expiry

    df = pd.DataFrame(raw)
    df = df[df["symbol"].str.contains(expiry_filter, regex=False, na=False)]

    df = df[
        (df["strike_price"] >= atm - STRIKE_RANGE_POINTS) &
        (df["strike_price"] <= atm + STRIKE_RANGE_POINTS)
    ]

    rows = {}

    for _, r in df.iterrows():
        strike = int(r.strike_price)
        opt = r.option_type
        oi = int(r.oi)
        ltp = float(r.ltp)
        key = f"{opt}_{strike}"

        prev_oi = st.session_state.prev_oi.get(key, 0)
        oi_pct = ((oi - prev_oi) / prev_oi * 100) if prev_oi >= MIN_BASE_OI else 0

        if strike not in rows:
            rows[strike] = {
                "Strike": strike,
                "CE OI %": 0, "CE LTP": 0,
                "PE OI %": 0, "PE LTP": 0,
                "ATM": strike == atm
            }

        if opt == "CE":
            rows[strike]["CE OI %"] = oi_pct
            rows[strike]["CE LTP"] = ltp
        else:
            rows[strike]["PE OI %"] = oi_pct
            rows[strike]["PE LTP"] = ltp

        st.session_state.prev_oi[key] = oi
        st.session_state.prev_ltp[key] = ltp

    df_view = pd.DataFrame(rows.values()).sort_values("Strike", ascending=False)

    st.subheader(f"📅 Weekly Expiry: {expiry}")
    st.dataframe(
        style_table(df_view),
        width="stretch",
        hide_index=True
    )

    if not st.session_state.warmed_up:
        st.session_state.warmed_up = True
        st.info("Baseline captured. Click again to detect spikes.")

# ================= UI =================
st.markdown("---")
if st.button("▶ Run OI Scan"):
    scan()

st.caption("No auto-refresh • No WebSocket • Streamlit Cloud safe")
