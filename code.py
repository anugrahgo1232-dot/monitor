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

# PASSWORD UNTUK MENJADI ADMIN VIA TELEGRAM
ADMIN_PASSWORD = "lorongdfamili"  # Ganti dengan kata sandi admin Anda

CONFIG_FILE = "config.json"
FLAGS_FILE = "flags.json"

# Flag global penanda alur monitoring/login
monitoring_started = False
monitoring_lock = threading.Lock()

# ==========================================
# MANAJEMEN CONFIG & STATE
# ==========================================
def load_config():
    default_config = {
        "group_chat_url": "https://t.me/username_grup_anda",
        "group_number_url": "https://t.me/username_grup_anda",
        "is_muted": False,
        "admin_user_ids": [],
        "target_chat_ids": [],
        "scheduled_broadcast_text": "",
        "scheduled_broadcast_interval_hours": 0  # 0 artinya nonaktif
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_config.update(data)
                return default_config
        except Exception:
            pass
    return default_config

def save_config(config_data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)

dynamic_config = load_config()
config_lock = threading.Lock()

# Antrean Auto Delete (24 jam)
pending_deletions = []
deletion_lock = threading.Lock()

# Cache pencarian OTP
otp_history = []
otp_history_lock = threading.Lock()

server_health = {"is_online": True, "last_error": None}
health_lock = threading.Lock()

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
# FUNGSI HELPER & TELEGRAM API
# ==========================================
def is_admin(user_id):
    with config_lock:
        admins = dynamic_config.get("admin_user_ids", [])
        return str(user_id) in [str(i) for i in admins]

def send_telegram_to_chat(text, chat_id, disable_notification=False, reply_to_message_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": text, 
        "parse_mode": "HTML",
        "disable_notification": disable_notification
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    try:
        res = requests.post(url, json=payload, timeout=10).json()
        if res.get("ok"):
            return res.get("result", {}).get("message_id")
    except Exception as e:
        print(f"[-] Gagal kirim pesan ke Chat ID {chat_id}: {e}")
    return None

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[-] Gagal edit pesan {message_id}: {e}")

def broadcast_to_all_groups(text, disable_notification=False):
    with config_lock:
        target_ids = list(dynamic_config.get("target_chat_ids", []))
    
    last_msg_id = None
    for cid in target_ids:
        msg_id = send_telegram_to_chat(text, cid, disable_notification)
        if msg_id:
            last_msg_id = msg_id
    return last_msg_id

def delete_telegram_message(chat_id, message_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
    payload = {"chat_id": chat_id, "message_id": message_id}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[-] Gagal menghapus pesan {message_id}: {e}")

def pin_telegram_message(chat_id, message_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/pinChatMessage"
    payload = {"chat_id": chat_id, "message_id": message_id, "disable_notification": False}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[-] Gagal menyematkan pesan: {e}")

def notify_health_change(status_online, error_msg=""):
    with health_lock:
        prev_status = server_health["is_online"]
        if prev_status == status_online:
            return
        server_health["is_online"] = status_online
        server_health["last_error"] = error_msg

    if not status_online:
        msg = (
            f"🚨 <b>HEALTH CHECK WARNING!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <b>Server Target Terputus / Down!</b>\n"
            f"📌 Detail Error: <code>{error_msg}</code>\n"
            f"🕒 Waktu: <b>{datetime.now().strftime('%H:%M:%S')}</b>\n"
            f"⚡ <i>Sistem mencoba melakukan re-koneksi otomatis...</i>"
        )
    else:
        msg = (
            f"🟢 <b>HEALTH CHECK RECOVERED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Koneksi ke Server Target Kembali Normal.</b>\n"
            f"🕒 Waktu: <b>{datetime.now().strftime('%H:%M:%S')}</b>"
        )
    
    broadcast_to_all_groups(msg)
    with config_lock:
        admin_list = list(dynamic_config.get("admin_user_ids", []))
    for admin_id in admin_list:
        send_telegram_to_chat(msg, chat_id=admin_id)

def generate_report_text(title="LIVE TRAFFIC REPORT"):
    with stats_lock:
        total_sms = traffic_stats["total_sms"]
        total_otp = traffic_stats["total_otp"]
        senders = dict(traffic_stats["senders"])

    now_str = datetime.now().strftime("%H:%M:%S")
    sender_details = ""
    if senders:
        for s_name, count in senders.items():
            icon = get_sender_icon(s_name)
            sender_details += f"• {icon} <b>{s_name}</b>: {count} SMS\n"
    else:
        sender_details = "• <i>Tidak ada aktivitas SMS</i>\n"

    return (
        f"📊 <b>{title}</b>\n"
        f"🕒 Waktu: <b>{now_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📥 Total SMS Masuk : <b>{total_sms}</b>\n"
        f"🔑 Total OTP Diterima: <b>{total_otp}</b>\n\n"
        f"<b>Rincian Layanan:</b>\n"
        f"{sender_details}"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <i>Monitoring Berjalan Normal</i>"
    )

# 📊 FITUR BARU: GENERATE MINI BAR CHART (ASCII GRAPH)
def generate_chart_text():
    with stats_lock:
        senders = dict(traffic_stats["senders"])
        total_sms = traffic_stats["total_sms"]

    now_str = datetime.now().strftime("%H:%M:%S")
    if not senders:
        return (
            f"📈 <b>SENDER TRAFFIC CHART</b>\n"
            f"🕒 Waktu: <b>{now_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Belum ada data pengirim (sender) tercatat.</i>"
        )

    # Cari nilai maksimum untuk skala bar chart (maks 10 blok)
    max_count = max(senders.values()) if senders else 1
    max_bars = 10

    chart_lines = ""
    for s_name, count in sorted(senders.items(), key=lambda item: item[1], reverse=True):
        icon = get_sender_icon(s_name)
        # Hitung jumlah kotak blok karakter (█)
        filled_blocks = int((count / max_count) * max_bars) if max_count > 0 else 0
        filled_blocks = max(1, filled_blocks) if count > 0 else 0
        
        bar = "█" * filled_blocks + "░" * (max_bars - filled_blocks)
        chart_lines.append(f"{icon} <b>{s_name[:10]:<10}</b> |{bar}| <b>{count}</b>")

    # Format string list manual
    chart_body = "\n".join([f"{icon} <b>{s_name[:12]}</b>: {'█' * int((count / max_count) * 8 if max_count > 0 else 1)} ({count})" for s_name, count in sorted(senders.items(), key=lambda x: x[1], reverse=True)])

    return (
        f"📈 <b>VISUAL SENDER TRAFFIC CHART</b>\n"
        f"🕒 Waktu: <b>{now_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📥 Total Keseluruhan SMS: <b>{total_sms}</b>\n\n"
        f"{chart_body}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <i>Skala proporsional berbasis aktivitas</i>"
    )

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

def save_to_otp_history(phone_raw, cli_raw, otp_code, sms_raw):
    with otp_history_lock:
        clean_num = re.sub(r'\D', '', phone_raw)
        otp_history.append({
            "phone_raw": phone_raw,
            "clean_num": clean_num,
            "cli_raw": cli_raw,
            "otp_code": otp_code,
            "sms_raw": sms_raw,
            "timestamp": datetime.now()
        })
        if len(otp_history) > 100:
            otp_history.pop(0)

def send_telegram_formatted(phone_raw, cli_raw, otp_code, lang_tag="EN"):
    with config_lock:
        if dynamic_config.get("is_muted", False):
            return
        chat_url = dynamic_config.get("group_chat_url", "https://t.me")
        number_url = dynamic_config.get("group_number_url", "https://t.me")
        target_ids = list(dynamic_config.get("target_chat_ids", []))

    if not target_ids:
        print("[-] Belum ada grup yang didaftarkan sebagai target OTP!")
        return

    flag_emoji, iso_code = get_country_info(phone_raw)
    sender_icon = get_sender_icon(cli_raw)
    masked_phone = format_masked_phone(phone_raw)
    header_text = f"{flag_emoji} {iso_code} {sender_icon} {masked_phone} #{lang_tag}"

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    for cid in target_ids:
        payload = {
            "chat_id": cid,
            "text": header_text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": f"📋 {otp_code}", "copy_text": {"text": otp_code}}],
                    [{"text": "CHAT", "url": chat_url}, {"text": "NUMBER", "url": number_url}]
                ]
            }
        }
        try:
            res = requests.post(url, json=payload, timeout=10).json()
            if res.get("ok"):
                msg_id = res.get("result", {}).get("message_id")
                with deletion_lock:
                    pending_deletions.append({
                        "chat_id": cid,
                        "message_id": msg_id,
                        "send_time": time.time()
                    })
        except Exception as e:
            print(f"[-] Gagal kirim pesan ke Chat ID {cid}: {e}")

# ==========================================
# THREAD PENCARIAN OTP (20 DETIK WAIT LOOP)
# ==========================================
def wait_search_otp_thread(chat_id, digits_query, command_msg_id):
    start_time = time.time()
    
    init_msg = (
        f"⏳ <b>MENUNGGU OTP ({digits_query})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Mencari OTP masuk untuk nomor akhiran <b>{digits_query}</b> (Maks 20 detik)...</i>"
    )
    sent_msg_id = send_telegram_to_chat(init_msg, chat_id=chat_id, reply_to_message_id=command_msg_id)
    if not sent_msg_id:
        return

    matched_target = None

    while time.time() - start_time < 20:
        with otp_history_lock:
            for item in reversed(otp_history):
                if item["clean_num"].endswith(digits_query):
                    matched_target = item
                    break

        if matched_target:
            break
        time.sleep(2)

    if matched_target:
        flag_emoji, iso_code = get_country_info(matched_target["phone_raw"])
        sender_icon = get_sender_icon(matched_target["cli_raw"])
        masked_phone = format_masked_phone(matched_target["phone_raw"])
        time_str = matched_target["timestamp"].strftime("%H:%M:%S")

        result_text = (
            f"🔍 <b>HASIL PENCARIAN OTP ({digits_query})</b>\n"
            f"🕒 <i>Diterima pada {time_str}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{flag_emoji} {iso_code} {sender_icon} {masked_phone} #EN"
        )
        reply_markup = {
            "inline_keyboard": [
                [{"text": f"📋 {matched_target['otp_code']}", "copy_text": {"text": matched_target['otp_code']}}]
            ]
        }
        edit_telegram_message(chat_id, sent_msg_id, result_text, reply_markup=reply_markup)
    else:
        fail_text = (
            f"❌ <b>OTP TIDAK DITEMUKAN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Tidak ada OTP masuk untuk nomor akhiran <b>{digits_query}</b> setelah menunggu 20 detik.</i>"
        )
        edit_telegram_message(chat_id, sent_msg_id, fail_text)

# ==========================================
# THREAD AUTO-DELETE NOTIFIKASI (24 JAM)
# ==========================================
def auto_delete_cleaner_thread():
    while True:
        time.sleep(60)
        now = time.time()
        to_remove = []

        with deletion_lock:
            for item in list(pending_deletions):
                if now - item["send_time"] >= 86400:
                    delete_telegram_message(item["chat_id"], item["message_id"])
                    to_remove.append(item)
            
            for item in to_remove:
                if item in pending_deletions:
                    pending_deletions.remove(item)

# ==========================================
# ⏰ THREAD SCHEDULED BROADCAST (BARU)
# ==========================================
def scheduled_broadcast_thread():
    while True:
        time.sleep(60) # Cek setiap 1 menit
        with config_lock:
            interval_hours = dynamic_config.get("scheduled_broadcast_interval_hours", 0)
            bc_text = dynamic_config.get("scheduled_broadcast_text", "")

        if interval_hours > 0 and bc_text:
            # Konversi jam ke detik
            interval_seconds = interval_hours * 3600
            # Gunakan file timestamp sederhana untuk mengecek kapan terakhir dikirim
            sch_file = "last_sched_bc.txt"
            last_sent = 0
            if os.path.exists(sch_file):
                try:
                    with open(sch_file, "r") as f:
                        last_sent = float(f.read().strip())
                except Exception:
                    pass

            if time.time() - last_sent >= interval_seconds:
                formatted_bc = f"📢 <b>PENGUMUMAN TERJADWAL</b>\n━━━━━━━━━━━━━━━━━━━\n\n{bc_text}"
                broadcast_to_all_groups(formatted_bc)
                
                with open(sch_file, "w") as f:
                    f.write(str(time.time()))

# ==========================================
# THREAD COMMAND LISTENER (ADMIN & GRUP)
# ==========================================
def telegram_command_listener():
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
                    chat_id = str(message.get("chat", {}).get("id"))
                    chat_type = message.get("chat", {}).get("type", "")
                    message_id = message.get("message_id")
                    text = message.get("text", "").strip()

                    if not text.startswith("/"):
                        continue

                    # 🔑 MENJADI ADMIN VIA PASSWORD
                    if text.startswith("/admin"):
                        parts = text.split(maxsplit=1)
                        if len(parts) > 1:
                            input_pass = parts[1].strip()
                            if input_pass == ADMIN_PASSWORD:
                                with config_lock:
                                    admins = dynamic_config.get("admin_user_ids", [])
                                    if str(user_id) not in [str(i) for i in admins]:
                                        admins.append(str(user_id))
                                        dynamic_config["admin_user_ids"] = admins
                                        save_config(dynamic_config)
                                send_telegram_to_chat("🎉 <b>BERHASIL!</b> Anda sekarang terdaftar sebagai Admin.", chat_id=chat_id, reply_to_message_id=message_id)
                            else:
                                send_telegram_to_chat("❌ <b>Kata Sandi Salah!</b> Akses ditolak.", chat_id=chat_id, reply_to_message_id=message_id)
                        else:
                            send_telegram_to_chat("⚠️ <b>Format Salah!</b> Gunakan:\n<code>/admin kata_sandi</code>", chat_id=chat_id, reply_to_message_id=message_id)
                        continue

                    # 🔒 CEK STATUS ADMIN DENGAN HAK AKSES STRICT
                    if not is_admin(user_id):
                        send_telegram_to_chat("⛔ <b>Akses Ditolak!</b> Anda belum menjadi admin.\nGunakan <code>/admin kata_sandi</code> untuk mendaftar.", chat_id=chat_id, reply_to_message_id=message_id)
                        continue

                    # 🚀 PEMICU /gas: KHUSUS ADMIN
                    if text in ["/gas", "/start"]:
                        if chat_type == "private":
                            send_telegram_to_chat("⚠️ Perintah <code>/gas</code> harus dijalankan <b>di dalam Grup Telegram</b> target.", chat_id=chat_id, reply_to_message_id=message_id)
                            continue

                        with config_lock:
                            groups = dynamic_config.get("target_chat_ids", [])
                            if chat_id not in groups:
                                groups.append(chat_id)
                                dynamic_config["target_chat_ids"] = groups
                                save_config(dynamic_config)

                        send_telegram_to_chat(
                            f"🚀 <b>PERINTAH /gas DITERIMA!</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 Admin: <code>{user_id}</code>\n"
                            f"📌 ID Grup: <code>{chat_id}</code>\n"
                            f"⚡ <i>Memulai proses login ke server web...</i>", 
                            chat_id=chat_id, 
                            reply_to_message_id=message_id
                        )
                        
                        global monitoring_started
                        with monitoring_lock:
                            if not monitoring_started:
                                monitoring_started = True
                                threading.Thread(target=start_monitoring_loop, daemon=True).start()
                        continue

                    # 🔍 FITUR CEK OTP
                    if text.startswith("/c ") or text.startswith("/cek "):
                        parts = text.split(maxsplit=1)
                        if len(parts) > 1:
                            digits_query = re.sub(r'\D', '', parts[1].strip())
                            if len(digits_query) < 3:
                                send_telegram_to_chat("⚠️ Harap masukkan minimal 3-4 digit nomor terakhir.", chat_id=chat_id, reply_to_message_id=message_id)
                                continue

                            threading.Thread(
                                target=wait_search_otp_thread,
                                args=(chat_id, digits_query, message_id),
                                daemon=True
                            ).start()
                        else:
                            send_telegram_to_chat("⚠️ <b>Format Salah!</b> Gunakan:\n<code>/c 4358</code> (4 digit terakhir)", chat_id=chat_id, reply_to_message_id=message_id)

                    # ➕ MENAMBAHKAN GRUP TARGET OTP
                    elif text in ["/addgroup", "/addgrup", "/setgroup", "/setgrup"]:
                        if chat_type == "private":
                            send_telegram_to_chat("⚠️ Perintah ini harus dijalankan <b>di dalam Grup Telegram</b> yang ingin didaftarkan.", chat_id=chat_id, reply_to_message_id=message_id)
                            continue

                        with config_lock:
                            groups = dynamic_config.get("target_chat_ids", [])
                            if chat_id not in groups:
                                groups.append(chat_id)
                                dynamic_config["target_chat_ids"] = groups
                                save_config(dynamic_config)
                                msg = f"✅ <b>Grup Berhasil Didaftarkan!</b>\nChat ID: <code>{chat_id}</code>\n<i>Notifikasi OTP akan dikirim ke grup ini.</i>"
                            else:
                                msg = f"ℹ️ Grup ini sudah terdaftar sebelumnya.\nChat ID: <code>{chat_id}</code>"
                        send_telegram_to_chat(msg, chat_id=chat_id, reply_to_message_id=message_id)

                    # 📜 LIST GRUP TARGET
                    elif text in ["/listgroup", "/listgrup"]:
                        if chat_type != "private":
                            send_telegram_to_chat("🔒 Perintah <code>/listgroup</code> hanya bisa digunakan via <b>Chat Pribadi (PM)</b> dengan bot.", chat_id=chat_id, reply_to_message_id=message_id)
                            continue

                        with config_lock:
                            groups = dynamic_config.get("target_chat_ids", [])
                        g_str = "\n".join([f"• <code>{g}</code>" for g in groups]) if groups else "<i>Belum ada grup yang didaftarkan.</i>"
                        send_telegram_to_chat(f"📢 <b>DAFTAR TARGET GRUP OTP:</b>\n━━━━━━━━━━━━━━━━━━━\n{g_str}", chat_id=chat_id, reply_to_message_id=message_id)

                    # ➖ MENGHAPUS GRUP TARGET OTP
                    elif text.startswith("/delgroup") or text.startswith("/delgrup") or text.startswith("/removegroup"):
                        if chat_type != "private":
                            send_telegram_to_chat("🔒 Perintah hapus grup hanya bisa digunakan via <b>Chat Pribadi (PM)</b> dengan bot.", chat_id=chat_id, reply_to_message_id=message_id)
                            continue

                        parts = text.split(maxsplit=1)
                        if len(parts) > 1:
                            target_del_id = parts[1].strip()
                            with config_lock:
                                groups = dynamic_config.get("target_chat_ids", [])
                                if target_del_id in groups:
                                    groups.remove(target_del_id)
                                    dynamic_config["target_chat_ids"] = groups
                                    save_config(dynamic_config)
                                    msg = f"🗑️ <b>Grup Berhasil Dihapus!</b>\nChat ID <code>{target_del_id}</code> tidak akan menerima OTP lagi."
                                else:
                                    msg = f"⚠️ Chat ID <code>{target_del_id}</code> tidak ditemukan dalam daftar grup."
                            send_telegram_to_chat(msg, chat_id=chat_id, reply_to_message_id=message_id)
                        else:
                            send_telegram_to_chat("⚠️ <b>Format Salah!</b> Gunakan di Chat Pribadi:\n<code>/delgroup &lt;chat_id_grup&gt;</code>", chat_id=chat_id, reply_to_message_id=message_id)

                    # KELUAR DARI ADMIN
                    elif text == "/unadmin":
                        with config_lock:
                            admins = dynamic_config.get("admin_user_ids", [])
                            if str(user_id) in [str(i) for i in admins]:
                                admins.remove(str(user_id))
                                dynamic_config["admin_user_ids"] = admins
                                save_config(dynamic_config)
                        send_telegram_to_chat("👋 Anda telah keluar dari status Admin.", chat_id=chat_id, reply_to_message_id=message_id)

                    # LIST ADMIN
                    elif text == "/listadmin":
                        with config_lock:
                            admins = dynamic_config.get("admin_user_ids", [])
                        admin_str = "\n".join([f"• <code>{a}</code>" for a in admins]) if admins else "<i>Belum ada admin terdaftar.</i>"
                        send_telegram_to_chat(f"👑 <b>DAFTAR USER ID ADMIN:</b>\n━━━━━━━━━━━━━━━━━━━\n{admin_str}", chat_id=chat_id, reply_to_message_id=message_id)

                    # 📖 DAFTAR MENU RAPI (/list & /help)
                    elif text in ["/list", "/help"]:
                        list_text = (
                            "🤖 <b>PANEL KONTROL BOT MONITORING</b>\n"
                            "━━━━━━━━━━━━━━━━━━━\n\n"
                            "🚀 <b>Eksekusi & Pencarian OTP:</b>\n"
                            "├ <code>/gas</code> : Jalankan alur login & daftarkan grup\n"
                            "└ <code>/c &lt;4_digit&gt;</code> : Cari OTP (tunggu maks 20 dtk)\n\n"
                            "📊 <b>Statistik & Laporan:</b>\n"
                            "├ <code>/report</code> : Laporan statistik traffic ringkas\n"
                            "└ <code>/chart</code> : Grafik batang (ASCII Bar Chart) sender\n\n"
                            "📢 <b>Pengumuman & Broadcast:</b>\n"
                            "├ <code>/bc &lt;pesan&gt;</code> : Broadcast instan & Pin ke semua grup\n"
                            "└ <code>/setbc &lt;jam&gt; &lt;pesan&gt;</code> : Set jadwal broadcast otomatis\n\n"
                            "🔕 <b>Kontrol Notifikasi & Sistem:</b>\n"
                            "├ <code>/mute</code> / <code>/unmute</code> : Matikan/Nyalakan notif\n"
                            "├ <code>/status</code> : Cek koneksi server & pengaturan\n"
                            "├ <code>/setchat &lt;url&gt;</code> : Ubah link tombol CHAT\n"
                            "└ <code>/setnumber &lt;url&gt;</code> : Ubah link tombol NUMBER\n\n"
                            "👑 <b>Manajemen Admin:</b>\n"
                            "├ <code>/admin &lt;pass&gt;</code> : Mendaftar sebagai admin\n"
                            "├ <code>/listadmin</code> : Cek daftar ID Admin\n"
                            "└ <code>/unadmin</code> : Keluar dari status Admin"
                        )
                        send_telegram_to_chat(list_text, chat_id=chat_id, reply_to_message_id=message_id)

                    # BROADCAST INSTAN
                    elif text.startswith("/bc") and not text.startswith("/bcset"):
                        parts = text.split(maxsplit=1)
                        if len(parts) > 1:
                            bc_msg = f"📢 <b>PENGUMUMAN ADMIN</b>\n━━━━━━━━━━━━━━━━━━━\n\n{parts[1]}"
                            with config_lock:
                                target_ids = list(dynamic_config.get("target_chat_ids", []))
                            for cid in target_ids:
                                msg_id = send_telegram_to_chat(bc_msg, chat_id=cid)
                                if msg_id:
                                    pin_telegram_message(cid, msg_id)
                            send_telegram_to_chat("✅ Broadcast instan berhasil dikirim & disematkan!", chat_id=chat_id, reply_to_message_id=message_id)
                        else:
                            send_telegram_to_chat("❌ Format salah! Gunakan:\n<code>/bc Pesan pengumuman</code>", chat_id=chat_id, reply_to_message_id=message_id)

                    # ⏰ MENGATUR SCHEDULED BROADCAST
                    elif text.startswith("/setbc") or text.startswith("/schedbc"):
                        parts = text.split(maxsplit=2)
                        if len(parts) >= 2:
                            try:
                                interval = int(parts[1])  # Dalam satuan jam
                                msg_content = parts[2] if len(parts) > 2 else ""
                                
                                with config_lock:
                                    dynamic_config["scheduled_broadcast_interval_hours"] = interval
                                    dynamic_config["scheduled_broadcast_text"] = msg_content
                                    save_config(dynamic_config)

                                if interval > 0:
                                    resp = f"✅ <b>Broadcast Terjadwal Diaktifkan!</b>\n⏱️ Interval: Setiap <b>{interval} jam</b> sekali.\n📝 Pesan:\n{msg_content}"
                                else:
                                    resp = "🛑 <b>Broadcast Terjadwal Dimatikan.</b>"
                                send_telegram_to_chat(resp, chat_id=chat_id, reply_to_message_id=message_id)
                            except ValueError:
                                send_telegram_to_chat("⚠️ Format angka jam salah! Gunakan:\n<code>/setbc 3 Halo pesan setiap 3 jam</code>", chat_id=chat_id, reply_to_message_id=message_id)
                        else:
                            send_telegram_to_chat("⚠️ Format salah! Gunakan:\n<code>/setbc &lt;jam&gt; &lt;pesan&gt;</code>\nContoh: <code>/setbc 2 Jangan lupa istirahat</code>\n(Ketik <code>/setbc 0 text</code> untuk mematikan)", chat_id=chat_id, reply_to_message_id=message_id)

                    # MUTE & UNMUTE
                    elif text == "/mute":
                        with config_lock:
                            dynamic_config["is_muted"] = True
                            save_config(dynamic_config)
                        send_telegram_to_chat("🔕 <b>Notifikasi OTP di-MUTE!</b>", chat_id=chat_id, reply_to_message_id=message_id)

                    elif text == "/unmute":
                        with config_lock:
                            dynamic_config["is_muted"] = False
                            save_config(dynamic_config)
                        send_telegram_to_chat("🔔 <b>Notifikasi OTP di-UNMUTE!</b>", chat_id=chat_id, reply_to_message_id=message_id)

                    # REPORT BIASA
                    elif text == "/report":
                        report_txt = generate_report_text(title="INSTANT TRAFFIC REPORT")
                        send_telegram_to_chat(report_txt, chat_id=chat_id, reply_to_message_id=message_id)

                    # 📊 /chart (MINI BAR CHART ASCII)
                    elif text == "/chart":
                        chart_txt = generate_chart_text()
                        send_telegram_to_chat(chart_txt, chat_id=chat_id, reply_to_message_id=message_id)

                    elif text.startswith("/setchat"):
                        parts = text.split(maxsplit=1)
                        if len(parts) > 1 and parts[1].startswith("http"):
                            new_url = parts[1].strip()
                            with config_lock:
                                dynamic_config["group_chat_url"] = new_url
                                save_config(dynamic_config)
                            send_telegram_to_chat(f"✅ Link Tombol CHAT diperbarui:\n{new_url}", chat_id=chat_id, reply_to_message_id=message_id)

                    elif text.startswith("/setnumber"):
                        parts = text.split(maxsplit=1)
                        if len(parts) > 1 and parts[1].startswith("http"):
                            new_url = parts[1].strip()
                            with config_lock:
                                dynamic_config["group_number_url"] = new_url
                                save_config(dynamic_config)
                            send_telegram_to_chat(f"✅ Link Tombol NUMBER diperbarui:\n{new_url}", chat_id=chat_id, reply_to_message_id=message_id)

                    elif text == "/status":
                        with config_lock:
                            c_url = dynamic_config.get("group_chat_url")
                            n_url = dynamic_config.get("group_number_url")
                            muted = "YA (🔕 Muted)" if dynamic_config.get("is_muted") else "TIDAK (🔔 Active)"
                            g_count = len(dynamic_config.get("target_chat_ids", []))
                            sched_interval = dynamic_config.get("scheduled_broadcast_interval_hours", 0)

                        with health_lock:
                            h_status = "🟢 ONLINE" if server_health["is_online"] else f"🔴 DOWN ({server_health['last_error']})"

                        msg = (
                            f"⚙️ <b>STATUS BOT & PENGATURAN:</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"🌐 <b>Server Health:</b> {h_status}\n"
                            f"🔇 <b>Mute State:</b> {muted}\n"
                            f"👥 <b>Total Grup Target:</b> {g_count} Grup\n"
                            f"⏰ <b>Scheduled Broadcast:</b> Setiap {sched_interval} jam sekali\n"
                            f"🟢 <b>CHAT URL:</b> {c_url}\n"
                            f"🔵 <b>NUMBER URL:</b> {n_url}"
                        )
                        send_telegram_to_chat(msg, chat_id=chat_id, reply_to_message_id=message_id)

        except Exception as e:
            print(f"[-] Error listener: {e}")

        time.sleep(2)

# ==========================================
# THREAD LIVE TRAFFIC (SETIAP 2 JAM)
# ==========================================
def traffic_reporter_thread():
    while True:
        time.sleep(7200)
        report_msg = generate_report_text(title="LIVE TRAFFIC REPORT (2 JAM)")
        
        with config_lock:
            target_ids = list(dynamic_config.get("target_chat_ids", []))
            
        for cid in target_ids:
            send_telegram_to_chat(report_msg, chat_id=cid)

        with stats_lock:
            traffic_stats["total_sms"] = 0
            traffic_stats["total_otp"] = 0
            traffic_stats["senders"].clear()
            traffic_stats["start_time"] = datetime.now()

def wait_for_telegram_answer():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset = None
    try:
        res = requests.get(url, params={"timeout": 5}, timeout=10).json()
        if res.get("ok") and res.get("result"):
            offset = res["result"][-1]["update_id"] + 1
    except Exception:
        pass

    print("[*] Menunggu jawaban CAPTCHA dari Telegram...")

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
        notify_health_change(True)

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
        notify_health_change(False, str(e))
        print(f"[-] Error sesskey: {e}")
    return ""

def do_login():
    print("\n[*] Mengakses halaman login...")
    try:
        res = session.get(LOGIN_URL, timeout=15)
        notify_health_change(True)
    except Exception as e:
        notify_health_change(False, str(e))
        print(f"[-] Gagal menghubungi server: {e}")
        return False

    soup = BeautifulSoup(res.text, "html.parser")
    captcha_text = "What is X + Y = ?"
    for tag in soup.find_all(string=True):
        if "what is" in tag.lower():
            captcha_text = tag.strip()
            break

    broadcast_to_all_groups(f"⚠️ <b>MEMBANTU LOGIN</b>\nCAPTCHA Server: <code>{captcha_text}</code>\n<i>Balas pesan ini dengan jawabannya.</i>")
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
        notify_health_change(False, str(e))
        print(f"[-] Error login post: {e}")
        return False
    
    if "login" in post_res.url.lower() or "signin" in post_res.url.lower():
        if "failed" in post_res.text.lower() or "invalid" in post_res.text.lower():
            print("[-] Login ditolak.")
            return False
        
    print("[+] Login BERHASIL!")
    return True

# ==========================================
# LOOP UTAMA MONITORING
# ==========================================
def start_monitoring_loop():
    processed_records = set()
    last_cleanup_time = time.time()

    threading.Thread(target=traffic_reporter_thread, daemon=True).start()
    
    print("[+] Proses login dipicu via /gas...")

    while not do_login():
        time.sleep(5)

    time.sleep(3)
    sesskey = fetch_sesskey()
    print(f"[+] Token sesskey: '{sesskey}'")

    while True:
        if time.time() - last_cleanup_time > 86400:
            processed_records.clear()
            last_cleanup_time = time.time()
            print("[*] Memori cache processed_records dibersihkan.")

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
            notify_health_change(True)
            
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

                    save_to_otp_history(phone_raw, cli_raw, otp_code, sms_raw)

                    send_telegram_formatted(phone_raw, cli_raw, otp_code, lang_tag="EN")
                    print(f"[+] TERKIRIM TELEGRAM -> {phone_raw} | OTP: {otp_code}")
                    processed_records.add(row_identifier)

        except requests.exceptions.RequestException as net_err:
            notify_health_change(False, str(net_err))
        except Exception as err:
            print(f"[-] Error: {err}")

        time.sleep(18)

if __name__ == "__main__":
    # 1. Jalankan background threads (Auto-delete, Scheduled Broadcast, & Listener)
    threading.Thread(target=auto_delete_cleaner_thread, daemon=True).start()
    threading.Thread(target=scheduled_broadcast_thread, daemon=True).start()
    
    # 2. Cek apakah sudah ada target chat_id tersimpan dari sesi sebelumnya
    with config_lock:
        existing_groups = dynamic_config.get("target_chat_ids", [])

    if existing_groups:
        print(f"[+] Ditemukan {len(existing_groups)} grup tersimpan. Memulai login secara otomatis...")
        with monitoring_lock:
            monitoring_started = True
            threading.Thread(target=start_monitoring_loop, daemon=True).start()
    else:
        print("[*] Belum ada grup tersimpan. Bot standby menunggu perintah /gas dari admin di grup...")

    telegram_command_listener()
