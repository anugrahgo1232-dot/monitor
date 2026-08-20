import html
import requests

def escape_html(text):
    if not text:
        return ""
    return html.escape(str(text))

def delete_webhook(token):
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    try:
        requests.get(url, params={"drop_pending_updates": True}, timeout=10)
    except Exception:
        pass

def get_telegram_updates(token, offset=0):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": offset, "timeout": 2}
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            return res.json().get("result", [])
    except Exception:
        pass
    return []

def send_telegram_message(token, chat_id, text, otp=None, reply_markup=None, group_url="https://t.me/link_grup_kamu", channel_url="https://t.me/link_channel_kamu"):
    """Mengirim pesan Telegram dengan dukungan custom reply_markup atau keyboard OTP otomatis."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup
    elif otp and otp not in ["-", "ACTIVE"]:
        keyboard = [
            [
                {
                    "text": str(otp),
                    "copy_text": {"text": str(otp)}
                }
            ],
            [
                {"text": "CHAT", "url": group_url},
                {"text": "NUMBER", "url": channel_url}
            ]
        ]
        payload["reply_markup"] = {"inline_keyboard": keyboard}

    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.json()
    except Exception as e:
        print("❌ Gagal kirim Telegram:", e)
        return None

def edit_telegram_message(token, chat_id, message_id, text, reply_markup=None):
    """Mengedit teks dan keyboard pada pesan yang sudah ada."""
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.json()
    except Exception as e:
        print("❌ Gagal edit pesan Telegram:", e)
        return None

def answer_callback_query(token, callback_query_id, text="", show_alert=False):
    """Merespons klik tombol inline (Callback Query)."""
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print("⚠️ Gagal answerCallbackQuery:", e)

def send_tg_reply(token, chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("⚠️ Gagal mengirim balasan Telegram:", e)

def send_tg_document(token, chat_id, file_path, caption=""):
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id, "caption": caption}
            requests.post(url, data=data, files=files, timeout=30)
    except Exception as e:
        print("⚠️ Gagal mengirim dokumen ke Telegram:", e)
