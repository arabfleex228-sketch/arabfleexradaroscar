import html
import json
import os
import re
import threading
import time
import binascii
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, quote

import requests
from curl_cffi import requests as curl_requests 
import urllib3
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from Crypto.Cipher import AES
import schedule

# تعطيل تحذيرات SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = "7808630939:AAEY0_q6vnkKlMRjvXNmEXwK1G80hv0vghY"
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "1013251619")
DATA_FILE = os.environ.get("DATA_FILE", "series.json")
# تم التعديل ليصبح الفحص كل 5 دقائق (300 ثانية) لتخفيف الضغط تماماً
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
SOURCE_DOMAINS = ["b2.shahidtv.net", "b1.shahidtv.net", "b3.shahidtv.net"]

API_URL = "https://arabfleex.live/api_bot.php"
SECRET_KEY = "ArabFleex_2024_SecRet"

# قائمة حسابات Cloudflare Workers الـ 3 الجديدة لتوزيع ضغط الفحص
CF_WORKERS = [
    "https://jolly-term-f45d.afu6656gu.workers.dev/?url=",
    "https://shy-snow-52c3.alifalah9988044.workers.dev/?url=",
    "https://young-glade-3a0e.sspw9f88.workers.dev/?url="
]

# تفعيل البوت
bot = telebot.TeleBot(BOT_TOKEN, threaded=True) # أعدنا تفعيل الـ threads لمنع تجمد البوت

scan_lock = threading.Lock()
started_at = datetime.now(timezone.utc)
last_scan_at = None
scan_cycles = 0
total_added = 0
last_scan_result = "لم يبدأ فحص بعد"

# ==========================================
# دالة تخطي حماية InfinityFree
# ==========================================
def get_infinity_session(url):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    try:
        res = session.get(url, timeout=15, verify=False)
        if "toNumbers" in res.text and "slowAES.decrypt" in res.text:
            a_match = re.search(r'a=toNumbers\("([a-f0-9]+)"\)', res.text)
            b_match = re.search(r'b=toNumbers\("([a-f0-9]+)"\)', res.text)
            c_match = re.search(r'c=toNumbers\("([a-f0-9]+)"\)', res.text)
            
            if a_match and b_match and c_match:
                key = binascii.unhexlify(a_match.group(1))
                iv = binascii.unhexlify(b_match.group(1))
                cipher = AES.new(key, AES.MODE_CBC, iv)
                decrypted = cipher.decrypt(binascii.unhexlify(c_match.group(1)))
                cookie_val = binascii.hexlify(decrypted).decode('utf-8')
                
                parsed_url = urlparse(url)
                session.cookies.set('__test', cookie_val, domain=parsed_url.netloc, path='/')
    except Exception as e:
        print(f"[ERROR] Infinity Session: {e}", flush=True)
    return session

def load_series_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as error:
        print(f"Error loading series data: {error}", flush=True)
        return {}

def save_series_data(data):
    parent = os.path.dirname(DATA_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temporary_file = f"{DATA_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, DATA_FILE)

# ==========================================
# توليد الروابط
# ==========================================
def candidate_urls_series(slug, season, episode, region):
    regions = list(dict.fromkeys([region, "EG", "LB", "SA", "SY", "MA"]))
    qualities = ["360p", "480p", "720p", "1080p"]
    episode_codes = [f"EP{episode:03d}", f"EP{episode:02d}"]
    suffixes = {q: [f"-{q}-v3.mp4", f"-{q}-v2.mp4", f"-{q}.mp4", f"-{q}-v1.mp4", f"-{q}-v4.mp4"] for q in qualities}
    for quality in qualities:
        for domain in SOURCE_DOMAINS:
            for item_region in regions:
                for episode_code in episode_codes:
                    for suffix in suffixes[quality]:
                        yield quality, f"https://{domain}/files/{item_region}/{slug}/{slug}-S{season:02d}-{episode_code}{suffix}"

def candidate_urls_wrestling(slug, date_str):
    qualities = ["360p", "480p", "720p", "1080p"]
    suffixes = {q: [f"-{q}-v3.mp4", f"-{q}-v2.mp4", f"-{q}.mp4", f"-{q}-v1.mp4", f"-{q}-v4.mp4"] for q in qualities}
    for quality in qualities:
        for domain in SOURCE_DOMAINS:
            for suffix in suffixes[quality]:
                yield quality, f"https://{domain}/files/wrestling/{slug}/{slug}-{date_str}{suffix}"

def probe_urls_series(slug, season, episode, region):
    domain = "b2.shahidtv.net"
    regions = list(dict.fromkeys([region, "EG"]))
    episode_codes = [f"EP{episode:03d}", f"EP{episode:02d}"]
    suffixes = ["-480p.mp4", "-480p-v2.mp4"]
    for r in regions:
        for ep_code in episode_codes:
            for suffix in suffixes:
                yield f"https://{domain}/files/{r}/{slug}/{slug}-S{season:02d}-{ep_code}{suffix}"

def probe_urls_wrestling(slug, date_str):
    domain = "b2.shahidtv.net"
    suffixes = ["-480p.mp4", "-480p-v2.mp4"]
    for suffix in suffixes:
        yield f"https://{domain}/files/wrestling/{slug}/{slug}-{date_str}{suffix}"

# ==========================================
# فحص الروابط
# ==========================================
def check_link(original_url):
    import random
    try:
        worker = random.choice(CF_WORKERS)
        test_url = f"{worker}{quote(original_url, safe='')}"
        response = curl_requests.get(test_url, impersonate="chrome", timeout=10, stream=True, verify=False)
        content_type = response.headers.get("Content-Type", "").lower()
        content_length = response.headers.get("Content-Length")
        
        valid_types = ["video/", "application/octet-stream", "application/force-download", "application/x-download"]
        has_video_type = not content_type or any(t in content_type for t in valid_types)
        
        if response.status_code != 200 or not has_video_type: return False
        if content_length and content_length.isdigit() and int(content_length) < 100_000: return False
            
        return True
    except Exception as e:
        return False

# ==========================================
# دالة حساب وقت الفحص (النوم الذكي)
# ==========================================
def is_time_to_scan(info):
    release_time_str = info.get("release_time")
    if not release_time_str: return True 
    
    try:
        release_time_str = release_time_str.strip().upper()
        if ":" in release_time_str: dt = datetime.strptime(release_time_str, "%I:%M %p")
        else: dt = datetime.strptime(release_time_str, "%I %p")
    except ValueError: return True

    egypt_tz = timezone(timedelta(hours=3))
    now = datetime.now(egypt_tz)
    now_mins = now.hour * 60 + now.minute
    target_mins = dt.hour * 60 + dt.minute
    diff = now_mins - target_mins
    if diff < -720: diff += 1440
    elif diff > 720: diff -= 1440
    
    if -60 <= diff <= 600: return True
    return False

# ==========================================
# عملية الفحص الأساسية
# ==========================================
def scan_item(slug, info):
    global last_scan_result
    
    if not is_time_to_scan(info):
        last_scan_result = f"{info.get('title', slug)}: خارج موعد النزول (في وضع النوم 💤)"
        return False
        
    item_type = info.get("type", "series")
    links = {}
    attempts = 0
    probe_found = False

    if item_type == "wrestling":
        last_date_str = info.get("last_date", "2026-01-01")
        last_ep = int(info.get("last_ep", 0))
        date_obj = datetime.strptime(last_date_str, "%Y-%m-%d")
        next_date_obj = date_obj + timedelta(days=7)
        target_date_to_scan = next_date_obj.strftime("%Y-%m-%d")
        target_episode = last_ep + 1
        
        for url in probe_urls_wrestling(slug, target_date_to_scan):
            attempts += 1
            if check_link(url):
                probe_found = True
                break
                
        if probe_found:
            for quality, url in candidate_urls_wrestling(slug, target_date_to_scan):
                if quality in links: continue
                attempts += 1
                if check_link(url): links[quality] = url
                
        display_title = target_date_to_scan.replace("-", ".")
    else:
        target_episode = int(info.get("last_ep", 0)) + 1
        season = int(info.get("season", 1))
        
        for url in probe_urls_series(slug, season, target_episode, str(info.get("region", "EG")).upper()):
            attempts += 1
            if check_link(url):
                probe_found = True
                break
                
        if probe_found:
            for quality, url in candidate_urls_series(slug, season, target_episode, str(info.get("region", "EG")).upper()):
                if quality in links: continue
                attempts += 1
                if check_link(url): links[quality] = url
                
        display_title = f"الحلقة {target_episode}"
        target_date_to_scan = None

    if not links:
        last_scan_result = f"{info.get('title', slug)}: الفحص لم يجد جديد (تم فحص {attempts} رابط)"
        return False

    title = info.get("title", slug)
    series_id = info.get("series_id")
    found_keys = list(links.keys())
    
    formatted_links = [f"{q.replace('p', '')}|{links[q]}" for q in ["360p", "480p", "720p", "1080p"] if q in links]
    links_string = ",".join(formatted_links)
    
    api_status = "لم يتم تحديد ID"
    if series_id:
        payload = {
            "secret_key": SECRET_KEY, "action": "insert", "series_id": series_id,
            "title": display_title, "episode_number": target_episode, "links_string": links_string
        }
        try:
            # هنا نرسل الطلب، إذا كان هناك جدار حماية سيتم قص رسالة الخطأ
            res = requests.post(API_URL, data=payload, timeout=20, verify=False)
            if "INSERTED" in res.text: api_status = "تمت الإضافة للموقع بنجاح ✅"
            elif "already exists" in res.text: api_status = "موجودة مسبقاً ⚠️"
            else: api_status = f"خطأ: {res.text[:100]}..." 
        except Exception as e: api_status = f"فشل الاتصال: {e}"

    msg = (
        f"🎬 <b>تم اصطياد وإضافة جديد:</b> {title}\n"
        f"📺 <b>{display_title}</b>\n"
        f"📶 <b>الجودات اللي نزلت:</b> {len(links)}/4 ({', '.join(found_keys)})\n"
        f"🌐 <b>الموقع:</b> {api_status}\n\n"
        f"✅ <i>تم قفل الحلقة والانتقال للبحث عن الحلقة القادمة...</i>"
    )
    
    try: bot.send_message(ADMIN_CHAT_ID, msg, parse_mode="HTML")
    except Exception as e: print(f"[ERROR] Failed to send Telegram message: {e}", flush=True)
    
    info["last_ep"] = target_episode
    if item_type == "wrestling": info["last_date"] = target_date_to_scan

    return True

def scan_all_series_once():
    global last_scan_at, scan_cycles, total_added
    if not scan_lock.acquire(blocking=False): return []
    try:
        scan_cycles += 1
        last_scan_at = datetime.now(timezone.utc)
        data = load_series_data()
        results = []
        for slug, info in list(data.items()):
            if scan_item(slug, info):
                total_added += 1
                save_series_data(data)
                results.append(f"{info.get('title', slug)}: تم التحديث ✅")
            else:
                results.append(last_scan_result)
            time.sleep(1.5) # تقليل مدة الانتظار قليلا
        return results
    except Exception as e:
        print(f"[ERROR] Error during full scan: {e}", flush=True)
        return []
    finally:
        scan_lock.release()

# ==========================================
# نظام الجدولة للفحص التلقائي
# ==========================================
def job_wrapper():
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Starting scheduled scan...", flush=True)
    scan_all_series_once()

schedule.every(CHECK_INTERVAL_SECONDS).seconds.do(job_wrapper)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

def format_duration(total_seconds):
    seconds = max(0, int(total_seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours}س {minutes}د {seconds}ث"

def status_message():
    uptime = (datetime.now(timezone.utc) - started_at).total_seconds()
    data = load_series_data()
    lines = [
        "✅ <b>البوت شغّال وبيفحص بانتظام!</b>\n",
        f"⏱ <b>وقت التشغيل:</b> {format_duration(uptime)}",
        f"🔍 <b>آخر فحص:</b> {last_scan_at.astimezone().strftime('%Y-%m-%d %H:%M:%S') if last_scan_at else 'لم يبدأ'}",
        f"🔄 <b>دورات الفحص:</b> {scan_cycles} | ➕ <b>الإشعارات:</b> {total_added}\n",
        "📺 <b>آخر حالة:</b>",
    ]
    if not data:
        lines.append("  لا توجد عناصر مضافة.")
    else:
        for slug, info in data.items():
            title = html.escape(str(info.get("title", slug)))
            series_id = info.get("series_id", "❌")
            rel_time = info.get("release_time", "طوال اليوم")
            state = "✅ مستعد"
            if info.get("type") == "wrestling":
                lines.append(f"  🥊 <b>{title}</b> (ID: {series_id}): آخر عرض {info.get('last_date')} | ⏱ {rel_time} | {state}")
            else:
                lines.append(f"  🎬 <b>{title}</b> (ID: {series_id}): حلقة {info.get('last_ep', 0)} | ⏱ {rel_time} | {state}")
    return "\n".join(lines)

def admin_only(message):
    return str(message.chat.id) == str(ADMIN_CHAT_ID)

@bot.message_handler(commands=["start", "help"])
def welcome(message):
    if admin_only(message):
        bot.reply_to(message, "🤖 <b>نظام المراقبة (سريع + دعم Worker)</b>\n\n🔹 <code>/add</code> — إضافة\n🔹 <code>/del</code> — حذف\n🔹 <code>/list</code> — قائمة\n🔹 <code>/setep</code> — تعديل حلقة\n🔹 <code>/setdate</code> — تعديل تاريخ\n🔹 <code>/settime</code> — موعد النزول ⏱\n🔹 <code>/check</code> — الحالة\n🔹 <code>/scan</code> — فحص يدوي\n🔹 <code>/test</code> — فحص رابط", parse_mode="HTML")

@bot.message_handler(commands=["backup"])
def backup_data(message):
    if not admin_only(message): return
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "rb") as f:
            bot.send_document(message.chat.id, f, caption="✅ نسخة احتياطية (series.json)")
    else: bot.reply_to(message, "⚠️ لا توجد بيانات للنسخ.")

@bot.message_handler(commands=["restore"])
def restore_data_step(message):
    if not admin_only(message): return
    msg = bot.reply_to(message, "📥 <b>أرسل لي ملف series.json كرسالة أو نص:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_restore)

def process_restore(message):
    if not admin_only(message): return
    raw_data = ""
    try:
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            raw_data = downloaded_file.decode('utf-8')
        elif message.text: raw_data = message.text
        else: return bot.reply_to(message, "❌ ملف أو نص غير صالح.")
        parsed_data = json.loads(raw_data)
        save_series_data(parsed_data)
        bot.reply_to(message, "✅ <b>تمت الاستعادة بنجاح!</b>", parse_mode="HTML")
    except Exception as e: bot.reply_to(message, f"❌ خطأ: {e}")

@bot.message_handler(commands=["add", "setep", "setdate", "settime", "list", "del", "check", "status", "test"])
def handle_commands(message):
    # تم تجميع الأوامر البسيطة اختصاراً للكود (الأوامر موجودة بالكامل في النسخة السابقة إذا احتجتها، ولكن لتنظيف الملف)
    pass # سيتم تجاهل هذه الأسطر الفارغة لتركيزنا على حل دالة /scan، يرجى الاحتفاظ بالدوال الأساسية الخاصة بك كما هي.

# [احتفظ بدوال add, setep, setdate, list, del, test, status بنفس الشكل الذي كانت عليه في الكود القديم]
# [لقد قمت بإخفائها هنا للتركيز على التعديلات الأهم حتى لا تتلخبط، ولكن سأكتب لك دالة scan المعدلة فقط:]

@bot.message_handler(commands=["scan"])
def force_check(message):
    if not admin_only(message): return
    
    # فحص إذا كان هناك فحص جارٍ الآن
    if scan_lock.locked():
        return bot.reply_to(message, "⏳ <b>يوجد فحص تلقائي أو يدوي قيد التشغيل حالياً، يرجى الانتظار...</b>", parse_mode="HTML")
    
    bot.reply_to(message, "🔎 <b>بدأ الفحص السريع عبر الـ Worker... (لن يتوقف البوت عن الاستجابة)</b>", parse_mode="HTML")
    
    # دالة داخلية تقوم بالفحص وإرسال النتيجة
    def background_manual_scan():
        try:
            results = scan_all_series_once()
            msg = "✅ <b>انتهى الفحص!</b>\n\n" + ("\n".join(results) or "📭 فارغ.")
            bot.send_message(ADMIN_CHAT_ID, msg, parse_mode="HTML")
        except Exception as e:
            bot.send_message(ADMIN_CHAT_ID, f"❌ حدث خطأ أثناء الفحص اليدوي: {e}")

    # تشغيل الفحص اليدوي في Thread منفصل لكي لا يُجمّد البوت!
    scan_thread = threading.Thread(target=background_manual_scan, daemon=True)
    scan_thread.start()

if __name__ == "__main__":
    print("Bot is starting... Cleaning up old webhooks/polling sessions.", flush=True)
    
    try:
        # مسح أي خطاف ويب قديم لمنع خطأ 409
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        print(f"Webhook cleanup error (Ignored): {e}")

    # تشغيل المجدول في خيط منفصل للبحث التلقائي
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    print("Bot is running with Fast Mode + CF Worker Download Proxy (STABLE VERSION)...", flush=True)
    
    # تشغيل البوت الأساسي
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"[ERROR] Polling crashed: {e}. Restarting in 5 seconds...", flush=True)
            time.sleep(5)
