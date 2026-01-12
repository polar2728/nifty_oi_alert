import streamlit as st
import pandas as pd
from datetime import datetime, time as dtime, timedelta, timezone
from fyers_apiv3 import fyersModel

# ================= PAGE CONFIG =================
st.set_page_config(page_title="NIFTY OI Spike Monitor", layout="wide")
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
    return True  # relaxed for cloud manual scan
    # return dtime(9, 15) <= t <= dtime(15, 30)

def reset_on_new_trading_day():
    today = now_ist().date()
    if st.session_state.last_run_date != today:
        st.session_state.prev_oi = {}
        st.session_state.prev_ltp = {}
        st.session_state.warmed_up = False
        st.session_state.last_run_date = today
        st.info("🔄 New trading day → baseline reset")

# ================= API =================
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
    return resp["data"]["optionsChain"], resp["data"]["expiryData"]

def expiry_to_symbol_format(date_str):
    try:
        d = datetime.strptime(date_str, "%d-%m-%Y")
        return d.strftime("%y") + str(d.month) + d.strftime("%d")
    except:
        return None

def get_current_weekly_expiry(expiry_list):
    today = now_ist().date()
    choices = []
    for e in expiry_list:
        try:
            d = datetime.fromtimestamp(int(e["expiry"])).date()
            choices.append(((d - today).days, e["date"]))
        except:
            pass
    return sorted(choices)[0][1] if choices else None

# ================= SCAN =================
def scan():
    reset_on_new_trading_day()

    spot = get_nifty_spot()
    if not spot:
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

    if df.empty:
        df = pd.DataFrame(raw)

    df = df[
        (df["strike_price"] >= atm - STRIKE_RANGE_POINTS) &
        (df["strike_price"] <= atm + STRIKE_RANGE_POINTS)
    ]

    if df.empty:
        st.warning("No strikes found")
        return

    # ========== BUILD CE–STRIKE–PE TABLE ==========
    rows = []

    for _, r in df.iterrows():
        strike = int(r.strike_price)
        opt = r.option_type
        oi = int(r.oi)
        ltp = float(r.ltp)
        key = f"{opt}_{strike}"

        prev_oi = st.session_state.prev_oi.get(key, 0)
        prev_ltp = st.session_state.prev_ltp.get(key, 0)

        oi_pct = ((oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0
        ltp_arrow = "↑" if ltp > prev_ltp else "↓" if prev_ltp else ""

        row = next((x for x in rows if x["STRIKE"] == strike), None)
        if not row:
            row = {
                "CALL OI": "",
                "CALL ΔOI": "",
                "CALL LTP": "",
                "STRIKE": strike,
                "PUT LTP": "",
                "PUT ΔOI": "",
                "PUT OI": "",
                "_ce_oi": 0,
                "_pe_oi": 0,
                "_atm": strike == atm
            }
            rows.append(row)

        if opt == "CE":
            row["CALL OI"] = f"{oi:,}"
            row["CALL ΔOI"] = f"{oi_pct:+.1f}%"
            row["CALL LTP"] = f"{ltp:.2f} {ltp_arrow}"
            row["_ce_oi"] = oi
        else:
            row["PUT OI"] = f"{oi:,}"
            row["PUT ΔOI"] = f"{oi_pct:+.1f}%"
            row["PUT LTP"] = f"{ltp:.2f} {ltp_arrow}"
            row["_pe_oi"] = oi

        st.session_state.prev_oi[key] = oi
        st.session_state.prev_ltp[key] = ltp

    table_df = pd.DataFrame(rows).sort_values("STRIKE")

    # ========== STYLING ==========
    def style_row(r):
        styles = []
        if r["_atm"]:
            styles.append("background-color:#fff8e1")
        if r["_ce_oi"] > r["_pe_oi"] * 1.2:
            styles.append("background-color:#eef6ff")
        elif r["_pe_oi"] > r["_ce_oi"] * 1.2:
            styles.append("background-color:#f1f8f5")
        return styles

    def style_cells(val):
        try:
            if "%" in val:
                pct = float(val.replace("%", ""))
                if pct > OI_SPIKE_THRESHOLD:
                    return "color:#b71c1c;font-weight:600"
        except:
            pass
        return ""

    styled = (
        table_df
        .drop(columns=["_ce_oi", "_pe_oi", "_atm"])
        .style
        .apply(style_row, axis=1)
        .applymap(style_cells, subset=["CALL ΔOI", "PUT ΔOI"])
    )

    st.subheader(f"📊 Option Chain (Expiry: {expiry})")
    st.dataframe(styled, use_container_width=True, hide_index=True)

    if not st.session_state.warmed_up:
        st.session_state.warmed_up = True
        st.info("Baseline captured. Click again to detect OI spikes.")

# ================= UI =================
st.markdown("---")
if st.button("▶ Run OI Scan"):
    scan()

st.caption("No auto-refresh • No WebSocket • Streamlit Cloud safe")
