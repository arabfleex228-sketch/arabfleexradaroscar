import json
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urljoin

import requests
from curl_cffi import requests as curl_requests
import urllib3
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import schedule
from bs4 import BeautifulSoup

# تعطيل تحذيرات SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = "7808630939:AAEY0_q6vnkKlMRjvXNmEXwK1G80hv0vghY"
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "1013251619")

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DATA_FILE = os.path.join(DATA_DIR, "series.json")

# الفحص كل دقيقتين ونص (150 ثانية)
CHECK_INTERVAL_SECONDS = 150
MAX_TRACKING_TIME_SECONDS = 3600 

API_URL = "https://arabfleex.live/api_bot.php"
SECRET_KEY = "ArabFleex_2024_SecRet"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

scan_lock = threading.Lock()
last_scan_at = None
scan_cycles = 0
total_added = 0

# دي القائمة اللي بتمنع البوت يتخدع في لاروزا!
BANNED_SERVERS = ['fembed', 'nitro', 'streamtape', 'arabseed', 'wecima']

def load_series_data():
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except: return {}

def save_series_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    temp_file = f"{DATA_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temp_file, DATA_FILE)

def send_telegram_msg(msg):
    try: bot.send_message(ADMIN_CHAT_ID, msg, parse_mode="HTML")
    except: pass

def fetch_html(url):
    try:
        res = curl_requests.get(url, impersonate="chrome", timeout=15, verify=False)
        return res.text
    except: return ""

def extract_vid(url):
    qs = parse_qs(urlparse(url).query)
    return qs.get('vid', [None])[0]

def is_valid_url(url):
    url_lower = url.lower()
    for ban in BANNED_SERVERS:
        if ban in url_lower: return False
    return True

def select_servers(watch_urls, down_urls):
    seen = set()
    w_pool = [x for x in watch_urls if is_valid_url(x) and not (x in seen or seen.add(x))]
    seen = set()
    d_pool = [x for x in down_urls if is_valid_url(x) and not (x in seen or seen.add(x))]

    def pop_match(pool, keywords):
        for kw in keywords:
            for url in pool:
                if kw.lower() in url.lower():
                    pool.remove(url)
                    return url
        return ""

    # ترتيب المشاهدة: liiivideo ثم uqload
    w1 = pop_match(w_pool, ['liiivideo', 'livideo'])
    if not w1: w1 = pop_match(w_pool, ['uqload'])

    w2 = pop_match(w_pool, ['uqload']) 
    if not w2: w2 = pop_match(w_pool, ['vidspeed'])

    w3 = pop_match(w_pool, ['rty', 'ok.ru', 'ok', 'vk.com', 'vk', 'anafast', 'vidmoly'])
    w4 = pop_match(w_pool, ['rty', 'ok.ru', 'ok', 'vk.com', 'vk', 'anafast', 'vidmoly'])
    
    if not w1 and w_pool: w1 = w_pool.pop(0)
    if not w2 and w_pool: w2 = w_pool.pop(0)
    if not w3 and w_pool: w3 = w_pool.pop(0)
    if not w4 and w_pool: w4 = w_pool.pop(0)

    # ترتيب التحميل: liiivideo ثم uqload
    d1 = pop_match(d_pool, ['liiivideo', 'livideo'])
    if not d1: d1 = pop_match(d_pool, ['1cloud'])

    d2 = pop_match(d_pool, ['uqload'])
    if not d2: d2 = pop_match(d_pool, ['voe'])
    
    if not d1 and d_pool: d1 = d_pool.pop(0)
    if not d2 and d_pool: d2 = d_pool.pop(0)

    return w1, w2, w3, w4, d1, d2

def get_laroza_ep(current_url, target_ep):
    html_content = fetch_html(current_url)
    if not html_content: return None
    soup = BeautifulSoup(html_content, 'html.parser')
    
    target_url = None
    
    for a in soup.select('.SeasonsEpisodes a'):
        em = a.find('em')
        if em and em.text.strip() == str(target_ep):
            target_url = urljoin(current_url, a.get('href'))
            break

    if not target_url:
        for option in soup.find_all('option'):
            text = option.text.strip()
            if f"الحلقة {target_ep}" in text or f"حلقة {target_ep}" in text:
                val = option.get('value')
                if val and val != "select-ep":
                    target_url = urljoin(current_url, val)
                    break
                    
    qs = parse_qs(urlparse(current_url).query)
    current_vid = qs.get('vid', [None])[0]
    if not target_url and current_vid and "video.php" in current_url: target_url = current_url
    if not target_url: return None
    vid = extract_vid(target_url)
    if not vid: return None
    
    play_url = urljoin(target_url, f"/play.php?vid={vid}")
    play_html = fetch_html(play_url)
    play_soup = BeautifulSoup(play_html, 'html.parser')
    watch_urls = [li.get('data-embed-url') for li in play_soup.select('ul.WatchList li') if li.get('data-embed-url')]
        
    dl_url = urljoin(target_url, f"/download.php?vid={vid}")
    dl_html = fetch_html(dl_url)
    dl_soup = BeautifulSoup(dl_html, 'html.parser')
    down_urls = [li.get('data-download-url') for li in dl_soup.select('ul.downloadlist li') if li.get('data-download-url')]
        
    # هنا بيتم تطبيق الفلتر! لو الروابط كلها fembed هتتحذف.
    valid_downs = [u for u in down_urls if is_valid_url(u)]
    
    # لو القائمة بقت فاضية بعد الفلتر، دي حلقة وهمية.
    if not valid_downs: return None

    return {"url": target_url, "watch_urls": watch_urls, "down_urls": valid_downs}

def get_qdrama_ep(current_url, target_ep):
    html_content = fetch_html(current_url)
    if not html_content: return None
    soup = BeautifulSoup(html_content, 'html.parser')
    
    target_url = None
    for a in soup.select('.AiredEPS a'):
        em = a.find('em')
        if em and em.text.strip() == str(target_ep):
            target_url = urljoin(current_url, a.get('href'))
            break
            
    qs = parse_qs(urlparse(current_url).query)
    current_vid = qs.get('vid', [None])[0]
    if not target_url and current_vid and "watch.php" in current_url: target_url = current_url
    if not target_url: return None
    vid = extract_vid(target_url)
    if not vid: return None
    
    play_url = urljoin(target_url, f"/play.php?vid={vid}")
    play_html = fetch_html(play_url)
    watch_urls = []
    servers_match = re.search(r'var\s+servers\s*=\s*(\[.*?\]);', play_html, re.DOTALL)
    if servers_match:
        try:
            servers_array = json.loads(servers_match.group(1))
            for item in servers_array:
                src_match = re.search(r'src=\\"(.*?)\\"', item) or re.search(r'src="(.*?)"', item)
                if src_match: watch_urls.append(src_match.group(1).replace('\\/', '/'))
        except: pass
            
    dl_url = urljoin(target_url, f"/download.php?vid={vid}")
    dl_html = fetch_html(dl_url)
    dl_soup = BeautifulSoup(dl_html, 'html.parser')
    down_urls = [a.get('href') for a in dl_soup.select('.download-servers-container a.download-btn, .special-download a.special-btn') if a.get('href')]
        
    valid_downs = [u for u in down_urls if is_valid_url(u)]
    if not valid_downs: return None
        
    return {"url": target_url, "watch_urls": watch_urls, "down_urls": valid_downs}

def scan_item(slug, info):
    target_ep = info["last_ep"] + 1
    
    if "tracking" not in info:
        info["tracking"] = {
            "episode": target_ep,
            "status": "waiting",
            "start_ts": time.time(),
            "watch_urls": [],
            "down_urls": [],
            "laroza_done": False,
            "qdrama_done": False
        }
        
    tracking = info["tracking"]
    new_discovery = False
    
    if info.get("laroza_url") and not tracking["laroza_done"]:
        res = get_laroza_ep(info["laroza_url"], target_ep)
        if res:
            tracking["watch_urls"].extend(res["watch_urls"])
            tracking["down_urls"].extend(res["down_urls"])
            tracking["laroza_done"] = True
            tracking["laroza_new_url"] = res["url"]
            new_discovery = True

    if info.get("qdrama_url") and not tracking["qdrama_done"]:
        res = get_qdrama_ep(info["qdrama_url"], target_ep)
        if res:
            tracking["watch_urls"].extend(res["watch_urls"])
            tracking["down_urls"].extend(res["down_urls"])
            tracking["qdrama_done"] = True
            tracking["qdrama_new_url"] = res["url"]
            new_discovery = True
                
    if new_discovery:
        action = "insert" if tracking["status"] == "waiting" else "update"
        tracking["status"] = "partial"
            
        w1, w2, w3, w4, d1, d2 = select_servers(tracking["watch_urls"], tracking["down_urls"])
        
        payload = {
            "secret_key": SECRET_KEY, "action": action, "series_id": info["series_id"],
            "title": f"الحلقة {target_ep}", "episode_number": target_ep,
            "watch_link": w1, "watch_link_2": w2, "watch_link_3": w3, "watch_link_4": w4,
            "download_link": d1, "download_link_2": d2
        }
        
        try:
            api_res = requests.post(API_URL, data=payload, timeout=30, verify=False)
            safe_api_status = api_res.text[:50].replace('<', '').replace('>', '')
        except Exception as e: safe_api_status = "خطأ اتصال"
            
        msg = f"🎬 <b>تم اصطياد وتحديث الحلقة {target_ep}</b>\n"
        msg += f"📺 <b>المسلسل:</b> {info['title']}\n"
        sources = []
        if tracking["laroza_done"]: sources.append("لاروزا ✅")
        if tracking["qdrama_done"]: sources.append("كيو دراما ✅")
        msg += f"📡 <b>المصادر:</b> " + " | ".join(sources) + "\n"
        msg += f"🌐 <b>حالة الـ API:</b> <code>{safe_api_status}</code>\n"
        send_telegram_msg(msg)

    if tracking["status"] != "waiting":
        is_timeout = (time.time() - tracking["start_ts"]) > MAX_TRACKING_TIME_SECONDS
        is_complete = True
        if info.get("laroza_url") and not tracking["laroza_done"]: is_complete = False
        if info.get("qdrama_url") and not tracking["qdrama_done"]: is_complete = False
        
        if is_complete or is_timeout:
            info["last_ep"] = target_ep
            if tracking.get("laroza_new_url"): info["laroza_url"] = tracking["laroza_new_url"]
            if tracking.get("qdrama_new_url"): info["qdrama_url"] = tracking["qdrama_new_url"]
            del info["tracking"]
            reason = "اكتملت جميع المصادر" if is_complete else "انتهت مهلة المراقبة (ساعة)"
            send_telegram_msg(f"🔒 <b>تم قفل الحلقة {target_ep}</b> ({info['title']})\nالسبب: {reason}")
            
    return True

def scan_all_series_once():
    global last_scan_at, scan_cycles, total_added
    if not scan_lock.acquire(blocking=False): return
    try:
        scan_cycles += 1
        last_scan_at = datetime.now(timezone.utc)
        data = load_series_data()
        for slug, info in list(data.items()):
            if scan_item(slug, info):
                total_added += 1
                save_series_data(data)
    except Exception as e: print(f"[ERROR] Error during full scan: {e}")
    finally: scan_lock.release()

schedule.every(CHECK_INTERVAL_SECONDS).seconds.do(scan_all_series_once)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

@bot.message_handler(commands=["start", "help"])
def welcome(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    bot.reply_to(message, "🤖 <b>نظام المراقبة (لاروزا + كيو دراما)</b>\n\n🔹 <code>/add</code> — إضافة مسلسل\n🔹 <code>/del</code> — حذف مسلسل\n🔹 <code>/status</code> — الحالة\n🔹 <code>/backup</code> — نسخة احتياطية 📥\n🔹 <code>/restore</code> — استعادة البيانات 📤", parse_mode="HTML")

@bot.message_handler(commands=["add"])
def add_item_start(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    msg = bot.reply_to(message, "📝 <b>أرسل اسم المسلسل:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, add_step_title)

def add_step_title(message):
    title = message.text.strip()
    msg = bot.reply_to(message, "🔢 <b>أرسل ID المسلسل في موقعك:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, add_step_id, title)

def add_step_id(message, title):
    try: series_id = int(message.text.strip())
    except: return bot.reply_to(message, "❌ يجب أن يكون رقماً.")
    msg = bot.reply_to(message, "🔢 <b>أرسل رقم آخر حلقة نزلت في موقعك دلوقتي:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, add_step_last_ep, title, series_id)

def add_step_last_ep(message, title, series_id):
    try: last_ep = int(message.text.strip())
    except: return bot.reply_to(message, "❌ يجب أن يكون رقماً.")
    msg = bot.reply_to(message, "🔗 <b>أرسل رابط آخر حلقة من موقع لاروزا</b>\n(الرابط لازم يحتوي على video.php)\nلو مش هتراقب لاروزا، اكتب <code>تخطي</code>:", parse_mode="HTML")
    bot.register_next_step_handler(msg, add_step_laroza, title, series_id, last_ep)

def add_step_laroza(message, title, series_id, last_ep):
    laroza_url = message.text.strip()
    if laroza_url != "تخطي" and "video.php" not in laroza_url:
        msg = bot.reply_to(message, "❌ الرابط لا يحتوي على video.php!\nأرسل رابط الحلقة الصحيح، أو اكتب <code>تخطي</code>", parse_mode="HTML")
        bot.register_next_step_handler(msg, add_step_laroza, title, series_id, last_ep)
        return
        
    if laroza_url == "تخطي": laroza_url = ""
    msg = bot.reply_to(message, "🔗 <b>أرسل رابط آخر حلقة من موقع كيو دراما</b>\n(الرابط لازم يحتوي على watch.php)\nلو مش هتراقب كيو دراما، اكتب <code>تخطي</code>:", parse_mode="HTML")
    bot.register_next_step_handler(msg, add_step_qdrama, title, series_id, last_ep, laroza_url)

def add_step_qdrama(message, title, series_id, last_ep, laroza_url):
    qdrama_url = message.text.strip()
    if qdrama_url != "تخطي" and "watch.php" not in qdrama_url:
        msg = bot.reply_to(message, "❌ الرابط لا يحتوي على watch.php!\nأرسل رابط الحلقة الصحيح، أو اكتب <code>تخطي</code>", parse_mode="HTML")
        bot.register_next_step_handler(msg, add_step_qdrama, title, series_id, last_ep, laroza_url)
        return
        
    if qdrama_url == "تخطي": qdrama_url = ""
    if not laroza_url and not qdrama_url:
        return bot.reply_to(message, "❌ لازم تحط رابط لموقع واحد على الأقل!")
        
    slug = f"series_{series_id}"
    data = load_series_data()
    data[slug] = {
        "title": title, "series_id": series_id, "last_ep": last_ep,
        "laroza_url": laroza_url, "qdrama_url": qdrama_url
    }
    save_series_data(data)
    bot.reply_to(message, "✅ تمت إضافة المسلسل بنجاح!\nالبوت مستعد لمراقبة الحلقة القادمة.", parse_mode="HTML")

@bot.message_handler(commands=["status", "list"])
def list_items(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    data = load_series_data()
    lines = [f"✅ <b>البوت شغال وبيفحص بانتظام!</b>\n🔄 دورات الفحص: {scan_cycles}\n"]
    if not data: lines.append("📭 لا توجد عناصر.")
    else:
        for slug, info in data.items():
            title = info.get("title", slug)
            last_ep = info.get("last_ep", 0)
            sources = []
            if info.get("laroza_url"): sources.append("لاروزا")
            if info.get("qdrama_url"): sources.append("كيو دراما")
            src_str = " + ".join(sources)
            status_txt = "مستعد ✅"
            if "tracking" in info:
                tr = info["tracking"]
                if tr["status"] == "waiting": status_txt = f"⏳ بانتظار {tr['episode']}"
                else: status_txt = f"🔄 بانتظار الباقي"
            lines.append(f"🎬 <b>{title}</b> (حلقة {last_ep}) | [{src_str}] | {status_txt}")
    bot.reply_to(message, "\n".join(lines), parse_mode="HTML")

@bot.message_handler(commands=["del"])
def delete_item(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    data = load_series_data()
    if not data: return bot.reply_to(message, "📭 القائمة فارغة.")
    markup = InlineKeyboardMarkup(row_width=1)
    for slug, info in data.items():
        markup.add(InlineKeyboardButton(text=f"❌ حذف: {info.get('title', slug)}", callback_data=f"del_{slug}"))
    bot.reply_to(message, "🗑 اختر المسلسل للحذف:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_'))
def process_delete_callback(call):
    slug = call.data.split('del_')[1]
    data = load_series_data()
    if slug in data:
        del data[slug]
        save_series_data(data)
        bot.answer_callback_query(call.id, "✅ تم الحذف بنجاح!")
        try: bot.edit_message_text(f"✅ تم الحذف.", call.message.chat.id, call.message.message_id)
        except: pass
    else:
        bot.answer_callback_query(call.id, "❌ غير موجود.")

@bot.message_handler(commands=["backup"])
def backup_data(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "rb") as f:
            bot.send_document(message.chat.id, f, caption="✅ <b>نسخة احتياطية (series.json)</b>", parse_mode="HTML")

@bot.message_handler(commands=["restore"])
def restore_data_step(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    msg = bot.reply_to(message, "📥 <b>أرسل لي ملف series.json:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_restore)

def process_restore(message):
    try:
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            dl_file = bot.download_file(file_info.file_path)
            save_series_data(json.loads(dl_file.decode('utf-8')))
            bot.reply_to(message, "✅ تمت الاستعادة!")
    except Exception as e: bot.reply_to(message, f"❌ خطأ: {e}")

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    threading.Thread(target=run_scheduler, daemon=True).start()
    while True:
        try: bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except: time.sleep(5)
