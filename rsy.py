import os
import json
import re
import time
import threading
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# ==========================================
# KONFIGURASI TARGET & KREDENSIAL
# ==========================================
BASE_URL = "http://176.9.58.30/ints"  # Ganti dengan IP/Domain target Anda
LOGIN_URL = f"{BASE_URL}/signin"
STATS_PAGE_URL = f"{BASE_URL}/client/SMSCDRStats"
AJAX_URL = f"{BASE_URL}/client/res/data_smscdr.php"

USERNAME = "alexmarth"
PASSWORD = "alexmarth"
TELEGRAM_TOKEN = "8833933759:AAGyGo2CqskLPBK1KuFyUluWxsbKgajVTGU"
TELEGRAM_CHAT_ID = "-1003914110525"  # ID Grup/Channel/Private tempat notifikasi dikirim

# MASUKKAN USER ID TELEGRAM ADMIN DI SINI (Hanya ID ini yang bisa ubah link via chat)
ADMIN_USER_ID = "8525945799"  # Contoh: "587654321"

# File Konfigurasi Link Dinamis
CONFIG_FILE = "config.json"
FLAGS_FILE = "flags.json"

# ==========================================
# MANAJEMEN CONFIG (LINK DYNAMIC)
# ==========================================
def load_config():
    default_config = {
        "group_chat_url": "https://t.me/username_grup_anda",
        "group_number_url": "https://t.me/username_grup_anda"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default_config

def save_config(config_data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)

dynamic_config = load_config()
config_lock = threading.Lock()

# ==========================================
# MEMATKAN / MEMBUAT PEMETAAN BENDERA
# ==========================================
def load_country_map():
    if os.path.exists(FLAGS_FILE):
        try:
            with open(FLAGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {k: tuple(v) for k, v in data.items()}
        except Exception:
            pass
    return {"62": ("🇮🇩", "ID")}

COUNTRY_MAP = load_country_map()

# Variable Global Laporan Traffic
traffic_stats = {
    "total_sms": 0,
    "total_otp": 0,
    "senders": {},
    "start_time": datetime.now()
}
stats_lock = threading.Lock()

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
})

# ==========================================
# FUNGSI HELPER & FORMAT PESAN TELEGRAM
# ==========================================
def send_telegram_simple(text, target_chat_id=None):
    cid = target_chat_id if target_chat_id else TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[-] Gagal kirim ke Telegram: {e}")

def get_country_info(phone_num):
    clean_num = re.sub(r'\D', '', phone_num)
    sorted_prefixes = sorted(COUNTRY_MAP.keys(), key=len, reverse=True)
    for prefix in sorted_prefixes:
        if clean_num.startswith(prefix):
            return COUNTRY_MAP[prefix]
    return ("🌐", "UN")

def get_sender_icon(cli_text):
    cli_upper = cli_text.strip().upper()
    if "WHATSAPP" in cli_upper or "WA" in cli_upper:
        return "🟢"
    elif "FACEBOOK" in cli_upper or "FB" in cli_upper:
        return "🔵"
    return "💬"

def format_masked_phone(phone_num):
    clean_num = re.sub(r'\D', '', phone_num)
    length = len(clean_num)
    if length > 6:
        mid = length // 2
        return f"{clean_num[:mid-1]}👑{clean_num[mid+1:]}"
    return clean_num

def send_telegram_formatted(phone_raw, cli_raw, otp_code, lang_tag="EN"):
    flag_emoji, iso_code = get_country_info(phone_raw)
    sender_icon = get_sender_icon(cli_raw)
    masked_phone = format_masked_phone(phone_raw)

    header_text = f"{flag_emoji} {iso_code} {sender_icon} {masked_phone} #{lang_tag}"

    with config_lock:
        chat_url = dynamic_config.get("group_chat_url", "https://t.me")
        number_url = dynamic_config.get("group_number_url", "https://t.me")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": header_text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": f"📋 {otp_code}",
                        "copy_text": {"text": otp_code}
                    }
                ],
                [
                    {
                        "text": "CHAT",
                        "url": chat_url
                    },
                    {
                        "text": "NUMBER",
                        "url": number_url
                    }
                ]
            ]
        }
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[-] Gagal kirim pesan ke Telegram: {e}")

# ==========================================
# THREAD COMMAND LISTENER (KHUSUS ADMIN)
# ==========================================
def telegram_command_listener():
    """Mendengarkan perintah /setchat dan /setnumber dari Chat Pribadi Admin"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset = None

    while True:
        try:
            params = {"timeout": 10}
            if offset is not None:
                params["offset"] = offset

            res = requests.get(url, params=params, timeout=15).json()

            if res.get("ok"):
                for update in res.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    user_id = str(message.get("from", {}).get("id"))
                    chat_id = message.get("chat", {}).get("id")
                    text = message.get("text", "").strip()

                    # CEK APAKAH PENGIRIM ADALAH ADMIN
                    if user_id != str(ADMIN_USER_ID):
                        if text.startswith("/"):
                            send_telegram_simple("⛔ <b>Akses Ditolak!</b> Anda bukan Admin.", target_chat_id=chat_id)
                        continue

                    # PERINTAH 1: Set Link Tombol CHAT
                    if text.startswith("/setchat"):
                        parts = text.split(maxsplit=1)
                        if len(parts) > 1 and parts[1].startswith("http"):
                            new_url = parts[1].strip()
                            with config_lock:
                                dynamic_config["group_chat_url"] = new_url
                                save_config(dynamic_config)
                            send_telegram_simple(f"✅ <b>Berhasil!</b> Link Tombol CHAT diperbarui ke:\n{new_url}", target_chat_id=chat_id)
                        else:
                            send_telegram_simple("❌ Format salah! Gunakan:\n<code>/setchat https://t.me/link_grup</code>", target_chat_id=chat_id)

                    # PERINTAH 2: Set Link Tombol NUMBER
                    elif text.startswith("/setnumber"):
                        parts = text.split(maxsplit=1)
                        if len(parts) > 1 and parts[1].startswith("http"):
                            new_url = parts[1].strip()
                            with config_lock:
                                dynamic_config["group_number_url"] = new_url
                                save_config(dynamic_config)
                            send_telegram_simple(f"✅ <b>Berhasil!</b> Link Tombol NUMBER diperbarui ke:\n{new_url}", target_chat_id=chat_id)
                        else:
                            send_telegram_simple("❌ Format salah! Gunakan:\n<code>/setnumber https://t.me/link_grup</code>", target_chat_id=chat_id)

                    # PERINTAH 3: Cek Status Link Saat Ini
                    elif text == "/status":
                        with config_lock:
                            c_url = dynamic_config.get("group_chat_url")
                            n_url = dynamic_config.get("group_number_url")
                        msg = f"⚙️ <b>CONFIG LINK SAAT INI:</b>\n\n🟢 <b>CHAT URL:</b> {c_url}\n🔵 <b>NUMBER URL:</b> {n_url}"
                        send_telegram_simple(msg, target_chat_id=chat_id)

        except Exception as e:
            print(f"[-] Error listener: {e}")

        time.sleep(2)

# ==========================================
# THREAD LIVE TRAFFIC (SETIAP 2 JAM)
# ==========================================
def traffic_reporter_thread():
    while True:
        time.sleep(7200)

        with stats_lock:
            total_sms = traffic_stats["total_sms"]
            total_otp = traffic_stats["total_otp"]
            senders = dict(traffic_stats["senders"])
            
            traffic_stats["total_sms"] = 0
            traffic_stats["total_otp"] = 0
            traffic_stats["senders"].clear()
            traffic_stats["start_time"] = datetime.now()

        now_str = datetime.now().strftime("%H:%M:%S")
        sender_details = ""
        if senders:
            for s_name, count in senders.items():
                icon = get_sender_icon(s_name)
                sender_details += f"• {icon} <b>{s_name}</b>: {count} SMS\n"
        else:
            sender_details = "• <i>Tidak ada aktivitas SMS</i>\n"

        traffic_msg = (
            f"📊 <b>LIVE TRAFFIC REPORT (2 JAM)</b>\n"
            f"🕒 Waktu: <b>{now_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📥 Total SMS Masuk : <b>{total_sms}</b>\n"
            f"🔑 Total OTP Diterima: <b>{total_otp}</b>\n\n"
            f"<b>Rincian Layanan:</b>\n"
            f"{sender_details}"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <i>Monitoring Berjalan Normal</i>"
        )
        send_telegram_simple(traffic_msg)

def wait_for_telegram_answer():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset = None
    try:
        res = requests.get(url, params={"timeout": 5}, timeout=10).json()
        if res.get("ok") and res.get("result"):
            offset = res["result"][-1]["update_id"] + 1
    except Exception:
        pass

    print("[*] Menunggu jawaban CAPTCHA dari Anda di Telegram...")

    while True:
        try:
            params = {"timeout": 10}
            if offset is not None:
                params["offset"] = offset

            response = requests.get(url, params=params, timeout=15).json()

            if response.get("ok"):
                for update in response.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    chat_id = str(message.get("chat", {}).get("id"))
                    text = message.get("text", "").strip()

                    if text and not text.startswith("/"):
                        print(f"[+] Jawaban CAPTCHA diterima: '{text}'")
                        return text
        except Exception:
            pass
        time.sleep(2)

def extract_otp(text):
    match_hyphen = re.search(r'\b(\d{3})[- ](\d{3})\b', text)
    if match_hyphen:
        return f"{match_hyphen.group(1)}{match_hyphen.group(2)}"
    
    match_digits = re.search(r'\b\d{4,8}\b', text)
    if match_digits:
        return match_digits.group(0)
    return None

def fetch_sesskey():
    try:
        res = session.get(STATS_PAGE_URL, timeout=15)
        match_url = re.search(r'(?:sesskey|diskey)=([A-Za-z0-9%=\-_]+)', res.text)
        if match_url:
            return match_url.group(1)
        
        soup = BeautifulSoup(res.text, "html.parser")
        for inp in soup.find_all("input"):
            name = (inp.get("name") or inp.get("id") or "").lower()
            if "sesskey" in name or "diskey" in name:
                val = inp.get("value")
                if val:
                    return val
    except Exception as e:
        print(f"[-] Error sesskey: {e}")
    return ""

def do_login():
    print("\n[*] Mengakses halaman login...")
    try:
        res = session.get(LOGIN_URL, timeout=15)
    except Exception as e:
        print(f"[-] Gagal menghubungi server: {e}")
        return False

    soup = BeautifulSoup(res.text, "html.parser")
    captcha_text = "What is X + Y = ?"
    for tag in soup.find_all(string=True):
        if "what is" in tag.lower():
            captcha_text = tag.strip()
            break

    send_telegram_simple(f"CAPTCHA: {captcha_text}")
    user_answer = wait_for_telegram_answer()

    form = soup.find("form")
    target_post_url = LOGIN_URL
    payload = {}

    if form:
        action = form.get("action")
        if action:
            target_post_url = urljoin(LOGIN_URL, action)

        for inp in form.find_all(["input", "button"]):
            name = inp.get("name")
            if not name:
                continue
            
            inp_type = inp.get("type", "").lower()
            val = inp.get("value", "")
            name_lower = name.lower()

            if "user" in name_lower:
                payload[name] = USERNAME
            elif "pass" in name_lower:
                payload[name] = PASSWORD
            elif any(k in name_lower for k in ["capt", "ans", "code"]):
                payload[name] = user_answer
            elif inp_type in ["submit", "button"]:
                payload[name] = val if val else "Log In"
            else:
                payload[name] = val

    headers_post = {
        "Referer": LOGIN_URL,
        "Origin": BASE_URL,
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        post_res = session.post(target_post_url, data=payload, headers=headers_post, timeout=15)
    except Exception as e:
        print(f"[-] Error login post: {e}")
        return False
    
    if "login" in post_res.url.lower() or "signin" in post_res.url.lower():
        if "failed" in post_res.text.lower() or "invalid" in post_res.text.lower():
            print("[-] Login ditolak.")
            return False
        
    print("[+] Login BERHASIL!")
    return True

def run_monitoring():
    processed_records = set()

    # Thread 1: Live Traffic
    t_traffic = threading.Thread(target=traffic_reporter_thread, daemon=True)
    t_traffic.start()

    # Thread 2: Perintah Admin Telegram
    t_cmd = threading.Thread(target=telegram_command_listener, daemon=True)
    t_cmd.start()
    print("[+] Feature Command Listener Admin AKTIF.")

    while not do_login():
        time.sleep(5)

    time.sleep(3)
    sesskey = fetch_sesskey()
    print(f"[+] Token sesskey: '{sesskey}'")

    while True:
        if not sesskey:
            time.sleep(10)
            sesskey = fetch_sesskey()
            if not sesskey:
                continue

        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
            params = {
                "fdate1": f"{yesterday_str} 00:00:00",
                "fdate2": f"{today_str} 23:59:59",
                "frange": "", "fnum": "", "fcli": "", "fgdate": "", "fgmonth": "",
                "fgrange": "", "fgnumber": "", "fgcli": "", "fg": "0",
                "sesskey": sesskey, "sEcho": "1", "iColumns": "7",
                "iDisplayStart": "0", "iDisplayLength": "50", "sSearch": "", "bRegex": "false"
            }

            headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Referer": STATS_PAGE_URL,
                "Accept": "application/json, text/javascript, */*; q=0.01"
            }

            res = session.get(AJAX_URL, params=params, headers=headers, timeout=15)
            
            if "signin" in res.url.lower() or "login" in res.url.lower():
                while not do_login():
                    time.sleep(5)
                time.sleep(3)
                sesskey = fetch_sesskey()
                continue

            if res.status_code == 503:
                sesskey = ""
                time.sleep(15)
                continue

            try:
                data_json = res.json()
            except Exception:
                data_json = None

            if data_json and "aaData" in data_json:
                rows_data = data_json.get("aaData", [])
                for item in rows_data:
                    if len(item) == 1 and "15 second" in str(item[0]):
                        time.sleep(18)
                        break

                    if len(item) < 5:
                        continue

                    phone_raw = BeautifulSoup(str(item[2]), "html.parser").get_text(strip=True)
                    cli_raw = BeautifulSoup(str(item[3]), "html.parser").get_text(strip=True)
                    sms_raw = BeautifulSoup(str(item[4]), "html.parser").get_text(strip=True)

                    if not phone_raw or "total" in phone_raw.lower():
                        continue

                    row_identifier = f"{phone_raw}_{sms_raw}"
                    if row_identifier in processed_records:
                        continue

                    with stats_lock:
                        traffic_stats["total_sms"] += 1
                        sender_name = cli_raw.strip().upper() if cli_raw else "UNKNOWN"
                        traffic_stats["senders"][sender_name] = traffic_stats["senders"].get(sender_name, 0) + 1

                    otp_code = extract_otp(sms_raw)
                    if not otp_code:
                        processed_records.add(row_identifier)
                        continue

                    with stats_lock:
                        traffic_stats["total_otp"] += 1

                    send_telegram_formatted(phone_raw, cli_raw, otp_code, lang_tag="EN")
                    print(f"[+] TERKIRIM TELEGRAM -> {phone_raw} | OTP: {otp_code}")
                    processed_records.add(row_identifier)

        except requests.exceptions.RequestException:
            pass
        except Exception as err:
            print(f"[-] Error: {err}")

        time.sleep(18)

if __name__ == "__main__":
    run_monitoring()
