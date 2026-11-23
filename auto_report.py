import os
import datetime
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from supabase import create_client

# 1. Lấy cấu hình từ GitHub Secrets
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Danh mục tài sản theo dõi
ASSETS = [
    {"name": "Vàng (XAU/USD)", "symbol": "GC=F", "display": "XAU/USD", "is_vn": False},
    {"name": "VN-Index", "symbol": "^VNINDEX", "display": "VN-INDEX", "is_vn": True},
    {"name": "FPT Corp", "symbol": "FPT.VN", "display": "FPT", "is_vn": True},
    {"name": "Hòa Phát", "symbol": "HPG.VN", "display": "HPG", "is_vn": True},
    {"name": "Vietcombank", "symbol": "VCB.VN", "display": "VCB", "is_vn": True},
    {"name": "Vingroup", "symbol": "VIC.VN", "display": "VIC", "is_vn": True},
]

def analyze_asset(asset):
    try:
        df = yf.download(asset["symbol"], period="6mo", interval="1d", progress=False)
        if df.empty or len(df) < 50:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        curr_price = float(close.iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])

        # Xác định Swing High / Swing Low 30 phiên gần nhất (SMC / Wyckoff / Fibo)
        swing_high = float(high.tail(30).max())
        swing_low = float(low.tail(30).min())
        price_range = swing_high - swing_low if swing_high != swing_low else 1.0
        fibo_pos = round((swing_high - curr_price) / price_range, 3)

        # Chấm điểm hợp lưu (Elliott + SMC + Wyckoff + MA)
        score = 50
        reasons = []

        # 1. Xu hướng MA
        if curr_price > ma20 > ma50:
            score += 20
            reasons.append("MA20>MA50 (Uptrend)")
        elif curr_price < ma20 < ma50:
            score -= 20
            reasons.append("MA20<MA50 (Downtrend)")
        else:
            reasons.append("MA Tích lũy")

        # 2. Vùng Fibonacci OTE / Discount - Premium (SMC)
        if 0.5 <= fibo_pos <= 0.786:
            score += 15
            reasons.append(f"Vùng Discount/OTE ({fibo_pos})")
        elif fibo_pos < 0.236:
            score -= 10
            reasons.append("Vùng Premium cao")

        # 3. Wyckoff Spring / Upthrust kiểm tra đáy/đỉnh 10 phiên
        recent_low = float(low.tail(10).min())
        if recent_low <= swing_low * 1.005 and curr_price > ma20:
            score += 15
            reasons.append("Wyckoff Spring xác nhận")

        score = max(10, min(95, score))

        # Khuyến nghị & Kế hoạch lệnh
        if score >= 70:
            rec = "MUA (BUY)"
            entry = round(curr_price, 2)
            sl = round(swing_low * 0.992, 2)
            tp = round(entry + (entry - sl) * 2.2, 2)
        elif score <= 35:
            rec = "BÁN / PHÒNG THỦ"
            entry = round(curr_price, 2)
            sl = round(swing_high * 1.008, 2)
            tp = round(entry - (sl - entry) * 2.0, 2)
        else:
            rec = "QUAN SÁT (WAIT)"
            entry = round(ma20, 2)
            sl = round(swing_low * 0.99, 2)
            tp = round(swing_high, 2)

        return {
            "symbol": asset["display"],
            "name": asset["name"],
            "current_price": round(curr_price, 2),
            "swing_high": round(swing_high, 2),
            "swing_low": round(swing_low, 2),
            "fibo_level": fibo_pos,
            "confluence_score": int(score),
            "recommendation": rec,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "notes": " | ".join(reasons)
        }
    except Exception as e:
        print(f"Lỗi phân tích {asset['display']}: {e}")
        return None

def main():
    vn_now = datetime.datetime.utcnow() + datetime.timedelta(hours=7)
    time_str = vn_now.strftime("%d/%m/%Y %H:%M")
    session_label = "PHIÊN SÁNG (08:00)" if vn_now.hour < 14 else "PHIÊN TỐI (20:00)"

    results = []
    for asset in ASSETS:
        res = analyze_asset(asset)
        if res:
            results.append(res)
            # Lưu vào bảng analysis_reports trên Supabase
            db_row = {
                "symbol": res["symbol"],
                "current_price": res["current_price"],
                "swing_high": res["swing_high"],
                "swing_low": res["swing_low"],
                "fibo_level": res["fibo_level"],
                "confluence_score": res["confluence_score"],
                "recommendation": res["recommendation"],
                "entry_price": res["entry_price"],
                "stop_loss": res["stop_loss"],
                "take_profit": res["take_profit"]
            }
            try:
                supabase.table("analysis_reports").insert(db_row).execute()
            except Exception as db_err:
                print("Lỗi ghi Supabase:", db_err)

    if not results:
        print("Không lấy được dữ liệu thị trường.")
        return

    # Tạo file báo cáo Excel
    excel_filename = f"AlphaDesk_Report_{vn_now.strftime('%Y%m%d_%H%M')}.xlsx"
    df_out = pd.DataFrame(results)
    df_out.to_excel(excel_filename, index=False)

    # Soạn tin nhắn báo cáo Telegram
    lines = [
        f"📊 <b>ALPHADESK PRO — BÁO CÁO {session_label}</b>",
        f"🕒 Thời gian: <code>{time_str} (GMT+7)</code>",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    for r in results:
        icon = "🟢" if "MUA" in r["recommendation"] else ("🔴" if "BÁN" in r["recommendation"] else "🟡")
        lines.append(
            f"{icon} <b>{r['symbol']}</b> | Giá: <b>{r['current_price']}</b>\n"
            f"   • Điểm hợp lưu: <b>{r['confluence_score']}/100</b> ({r['notes']})\n"
            f"   • Hành động: <b>{r['recommendation']}</b>\n"
            f"   • Entry: <code>{r['entry_price']}</code> | SL: <code>{r['stop_loss']}</code> | TP: <code>{r['take_profit']}</code>"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━\n📎 <i>Đính kèm chi tiết bảng tính Excel bên dưới.</i>")
    message_text = "\n".join(lines)

    # Gửi tin nhắn văn bản tới Telegram
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message_text, "parse_mode": "HTML"}
    )

    # Gửi đính kèm file Excel tới Telegram
    with open(excel_filename, "rb") as f:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": f"📑 Báo cáo chi tiết AlphaDesk ({time_str})"},
            files={"document": f}
        )

if __name__ == "__main__":
    main()
