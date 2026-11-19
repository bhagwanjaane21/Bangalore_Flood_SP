import time
import requests

# ----------------- CONFIGURATION -----------------
TELEGRAM_BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "PASTE_YOUR_CHAT_ID_HERE"
HOST_IP = "192.168.1.18"  # Change to e.g. "192.168.1.15" for phone access
DASHBOARD_URL = f"http://{HOST_IP}:8000/"
API_STATUS_URL = "http://127.0.0.1:8000/api/sensors/status"

# Tracks sent alert states to avoid spamming the same alert
last_alert_state = {}

def send_telegram_alert(message_html: str):
    """Broadcasts a high-priority alert card to Telegram using HTML parsing."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[CONSOLE ALERT (Telegram credentials missing)]\n" + message_html)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_html,
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(url, json=payload, timeout=5)
        if res.status_code == 200:
            print("📲 Alert delivered to Telegram successfully!")
        else:
            print(f"⚠️ Telegram API response: {res.text}")
    except Exception as e:
        print(f"❌ Failed to reach Telegram: {e}")

def monitor_and_dispatch():
    """Polls backend status and sends alerts when thresholds escalate."""
    global last_alert_state
    print("🔔 STREAM Telegram Alert Dispatcher is running...")
    
    while True:
        try:
            res = requests.get(API_STATUS_URL, timeout=3)
            if res.status_code == 200:
                data = res.json()
                for node_id, sensor in data.items():
                    level = sensor["alert_level"]
                    prev_level = last_alert_state.get(node_id, "GREEN")

                    # Fire notification if level escalates to YELLOW, ORANGE, or RED
                    if level != prev_level and level in ["YELLOW", "ORANGE", "RED"]:
                        emojis = {"YELLOW": "📢", "ORANGE": "⚠️", "RED": "🚨"}
                        action_text = {
                            "YELLOW": "Security & facilities on standby. Monitor 1.6 km bypass intake for debris.",
                            "ORANGE": "High surge moving down valley. Clear low-lying lanes; reroute commuters to Bellary Rd.",
                            "RED": "CRITICAL SURGE. Lower flood barriers at Gate 4. Activate pumps. Evacuate basement parking."
                        }

                        current_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        alert_msg = (
                            f"{emojis[level]} <b>{level} ALERT: {node_id}</b>\n"
                            f"📍 <b>Location:</b> {sensor['name']}\n"
                            f"📏 <b>Water Depth:</b> {sensor['water_depth_m']:.2f} m (+{sensor['rate_of_rise_cm_min']} cm/min)\n"
                            f"⏳ <b>Lead Time to Impact:</b> {sensor['lead_time']}\n\n"
                            f"📋 <b>Action Protocol:</b>\n{action_text[level]}\n\n"
                            f"🕒 <i>Dispatched: {current_ts}</i>"
                        )
                        send_telegram_alert(alert_msg)
                        last_alert_state[node_id] = level
                    elif level == "GREEN":
                        last_alert_state[node_id] = "GREEN"
        except Exception:
            pass
        time.sleep(3)

if __name__ == "__main__":
    monitor_and_dispatch()