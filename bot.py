import html
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
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

# إعدادات البوت والـ API
BOT_TOKEN = "7808630939:AAESznQOSKVU9xFeDSk2OQZpOELP3P0sRas"
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "1013251619")

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DATA_FILE = os.path.join(DATA_DIR, "series.json")

CHECK_INTERVAL_SECONDS = 150
MAX_TRACKING_TIME_SECONDS = 3600

API_URL = "https://arabfleex.live/api_bot.php"
SECRET_KEY = "ArabFleex_2024_SecRet"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

scan_lock = threading.Lock()
last_scan_at = None
scan_cycles = 0
total_added = 0

# قائمة حظر سيرفرات الإعلانات والروابط الوهمية
BANNED_SERVERS = ['fembed', 'nitro', 'streamtape', 'arabseed', 'wecima']

# قائمة دومينات لاروزا للبحث التلقائي عند تعطل الدومين الحالي
LAROZA_KNOWN_DOMAINS = [
    "https://larooza.asia",
    "https://laroza.lat",
    "https://larozza.forum",
    "https://larozza.beer",
    "https://larroza.baby",
    "https://larroza.click",
    "https://larroza.casa",
]

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
    if not url: return False
    url_lower = url.lower()
    for ban in BANNED_SERVERS:
        if ban in url_lower: return False
    return True

def get_working_laroza_url(url):
    """التحقق من رابط لاروزا، وإن كان الدومين معطلاً يبحث عن الدومين البديل من القائمة"""
    if not url: return None
    # التجربة بالرابط الحالي أولاً
    try:
        res = curl_requests.get(url, impersonate="chrome", timeout=10, verify=False)
        if res.status_code == 200 and ("video.php" in res.text or "view-serie" in res.text):
            return url
    except: pass

    # إذا فشل، نجرب استبدال الدومين بالدومينات المعروفة
    parsed = urlparse(url)
    path_query = parsed.path
    if parsed.query: path_query += "?" + parsed.query
    
    for domain in LAROZA_KNOWN_DOMAINS:
        test_url = urljoin(domain, path_query)
        try:
            res = curl_requests.get(test_url, impersonate="chrome", timeout=10, verify=False)
            if res.status_code == 200 and ("video.php" in res.text or "view-serie" in res.text):
                return test_url
        except: continue
    return None

def extract_video_fingerprint(url):
    if not url: return None
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        parts = [p for p in parsed.path.strip('/').lower().split('/') if p]
        if len(parts) >= 2:
            return f"{netloc}/{parts[0]}/{parts[1]}"
        base = f"{netloc}/{parts[0]}" if parts else netloc
        if parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=False)
            id_params = ['id', 'oid', 'v', 'vid', 'file', 'i', 'key', 'uid', 'code']
            id_parts = []
            for p in id_params:
                if p in qs:
                    id_parts.append(f"{p}={qs[p][0].lower()}")
            if id_parts:
                return f"{base}?{'&'.join(id_parts)}"
            first_key = sorted(qs.keys())[0]
            return f"{base}?{first_key}={qs[first_key][0][:40].lower()}"
        return base
    except:
        return None

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

    # الترتيب المحدث للمشاهدة حسب طلبك:
    # 1. vidspeed (أساسي)
    # 2. mp4plus (أساسي)
    # 3. uqload
    # 4. ok أو vk
    w1 = pop_match(w_pool, ['vidspeed'])
    w2 = pop_match(w_pool, ['mp4plus'])
    w3 = pop_match(w_pool, ['uqload'])
    w4 = pop_match(w_pool, ['ok.ru', 'ok', 'vk.com', 'vk'])
    
    # ملء أي فراغات باقية من السيرفرات المتاحة إذا لم تتوفر السيرفرات المطلوبة
    if not w1 and w_pool: w1 = w_pool.pop(0)
    if not w2 and w_pool: w2 = w_pool.pop(0)
    if not w3 and w_pool: w3 = w_pool.pop(0)
    if not w4 and w_pool: w4 = w_pool.pop(0)

    # ترتيب التحميل (بقي كما هو بناءً على طلبك)
    d1 = pop_match(d_pool, ['liiivideo', 'livideo'])
    if not d1: d1 = pop_match(d_pool, ['uqload'])

    d2 = pop_match(d_pool, ['uqload'])
    if not d2: d2 = pop_match(d_pool, ['1cloud'])
    
    if not d1 and d_pool: d1 = d_pool.pop(0)
    if not d2 and d_pool: d2 = d_pool.pop(0)

    return w1, w2, w3, w4, d1, d2

def get_laroza_ep(current_url, target_ep, seen_fps):
    html_content = fetch_html(current_url)
    if not html_content: return None
    soup = BeautifulSoup(html_content, 'html.parser')
    
    target_url = None
    active_div = None
    
    # 1. تحديد الموسم النشط عشان نتجاهل المواسم القديمة تماماً (حتى لا تتداخل الحلقات)
    active_tab = soup.select_one('.SeasonsBoxUL li.active')
    if active_tab and active_tab.has_attr('data-serie'):
        active_serie = active_tab['data-serie']
        active_div = soup.select_one(f'.SeasonsEpisodes[data-serie="{active_serie}"]')

    # لو ملقاش التاب النشط، يدور على المربع اللي مش مخفي
    if not active_div:
        for div in soup.select('.SeasonsEpisodes'):
            if 'display:none' not in div.get('style', '').replace(' ', ''):
                active_div = div
                break

    # البحث حصرياً داخل الموسم النشط لتجنب حلقات المواسم القديمة
    search_container = active_div if active_div else soup
    
    for a in search_container.select('a'):
        if 'video.php' not in a.get('href', ''): continue
        em = a.find('em')
        if em and em.text.strip() == str(target_ep):
            target_url = urljoin(current_url, a.get('href'))
            break

    # دعم الموبايل (البحث في القائمة المنسدلة للموسم النشط)
    if not target_url:
        mobile_container = soup
        active_mob = soup.select_one('select#mobileselect option.mactive')
        if active_mob and active_mob.has_attr('value'):
            mob_id = active_mob['value']
            mobile_container = soup.select_one(f'select#{mob_id}') or soup

        for option in mobile_container.find_all('option'):
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
        
    valid_downs = [u for u in down_urls if is_valid_url(u)]
    valid_watches = [u for u in watch_urls if is_valid_url(u)]
    
    # حماية 1: درع التحميل (إذا كانت حلقة بدون سيرفرات تحميل مفلترة ونظيفة، فهي حلقة وهمية)
    if not valid_downs: return None

    # حماية 2: درع البصمة (استخراج بصمة السيرفرات ومقارنتها لمنع الحلقات الوهمية ذات الإعلانات المكررة)
    for link in valid_watches:
        fp = extract_video_fingerprint(link)
        if fp and (fp in seen_fps):
            return None 

    return {"url": target_url, "watch_urls": valid_watches, "down_urls": valid_downs}

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
    valid_watches = [u for u in watch_urls if is_valid_url(u)]
    
    # حماية كيو دراما: درع التحميل (إذا كانت حلقة بدون سيرفرات تحميل نظيفة، فهي حلقة وهمية)
    if not valid_downs: return None
        
    return {"url": target_url, "watch_urls": valid_watches, "down_urls": valid_downs}

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
    
    # فحص لاروزا مع البصمة وتبديل الدومين التلقائي
    if info.get("laroza_url") and not tracking["laroza_done"]:
        # تحديث الدومين إذا لزم الأمر
        working_url = get_working_laroza_url(info["laroza_url"])
        if working_url:
            if working_url != info["laroza_url"]:
                info["laroza_url"] = working_url # حفظ الدومين الجديد
                
            if "laroza_seen_fps" not in info:
                info["laroza_seen_fps"] = []
                
            res = get_laroza_ep(info["laroza_url"], target_ep, info["laroza_seen_fps"])
            if res:
                # إضافة بصمات الحلقة الجديدة للحماية في المستقبل
                for u in res["watch_urls"]:
                    fp = extract_video_fingerprint(u)
                    if fp and fp not in info["laroza_seen_fps"]:
                        info["laroza_seen_fps"].append(fp)
                # الحفاظ على آخر 50 بصمة لعدم تضخم الملف
                info["laroza_seen_fps"] = info["laroza_seen_fps"][-50:]

                tracking["watch_urls"].extend(res["watch_urls"])
                tracking["down_urls"].extend(res["down_urls"])
                tracking["laroza_done"] = True
                tracking["laroza_new_url"] = res["url"]
                new_discovery = True

    # فحص كيو دراما
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
        changed = False
        for slug, info in list(data.items()):
            if scan_item(slug, info):
                total_added += 1
                changed = True
        if changed:
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
        
    laroza_seen_fps = []
    if laroza_url:
        bot.send_message(message.chat.id, "⏳ جاري حفظ بصمات لاروزا الحالية للحماية...")
        # تحديث الدومين أثناء الإضافة لضمان حفظ رابط صالح
        working_url = get_working_laroza_url(laroza_url)
        if working_url: laroza_url = working_url

        try:
            qs = parse_qs(urlparse(laroza_url).query)
            vid = qs.get('vid', [None])[0]
            if vid:
                play_url = urljoin(laroza_url, f"/play.php?vid={vid}")
                play_html = fetch_html(play_url)
                play_soup = BeautifulSoup(play_html, 'html.parser')
                w_urls = [li.get('data-embed-url') for li in play_soup.select('ul.WatchList li') if li.get('data-embed-url')]
                for u in w_urls:
                    if is_valid_url(u):
                        fp = extract_video_fingerprint(u)
                        if fp: laroza_seen_fps.append(fp)
        except: pass

    slug = f"series_{series_id}"
    data = load_series_data()
    data[slug] = {
        "title": title, "series_id": series_id, "last_ep": last_ep,
        "laroza_url": laroza_url, "qdrama_url": qdrama_url,
        "laroza_seen_fps": laroza_seen_fps
    }
    save_series_data(data)
    bot.reply_to(message, f"✅ تمت إضافة المسلسل بنجاح!\nالبوت مستعد لمراقبة حلقة {last_ep + 1}.", parse_mode="HTML")

@bot.message_handler(commands=["status", "list"])
def list_items(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    data = load_series_data()
    lines = [f"✅ <b>البوت شغال وبيفحص بانتظام!</b>\n🔄 دورات الفحص: {scan_cycles}\n"]
    if not data: lines.append("📭 لا توجد عناصر.")
    else:
        for slug, info in data.items():
            title = html.escape(str(info.get("title", slug))) # تنظيف الاسم لحماية التنسيق
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
        title = info.get('title', slug)
        markup.add(InlineKeyboardButton(text=f"❌ حذف: {title}", callback_data=f"del_{slug}"))
    bot.reply_to(message, "🗑 اختر المسلسل للحذف:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_'))
def process_delete_callback(call):
    slug = call.data.split('del_')[1]
    data = load_series_data()
    if slug in data:
        title = data[slug].get("title", slug)
        del data[slug]
        save_series_data(data)
        bot.answer_callback_query(call.id, f"✅ تم حذف {title}")
        try: bot.edit_message_text(f"✅ تم حذف مسلسل: {title}", call.message.chat.id, call.message.message_id)
        except: pass
    else:
        bot.answer_callback_query(call.id, "❌ غير موجود.")

@bot.message_handler(commands=["backup"])
def backup_data(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "rb") as f:
            bot.send_document(message.chat.id, f, caption="✅ <b>نسخة احتياطية (series.json)</b>", parse_mode="HTML")
    else:
        bot.reply_to(message, "❌ لا يوجد ملف بيانات حالياً.")

@bot.message_handler(commands=["restore"])
def restore_data_step(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    msg = bot.reply_to(message, "📥 <b>أرسل لي ملف series.json أو انسخ محتواه كنص هنا:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_restore)

def process_restore(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    try:
        raw_data = ""
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            dl_file = bot.download_file(file_info.file_path)
            raw_data = dl_file.decode('utf-8')
        elif message.text:
            raw_data = message.text
        else:
            return bot.reply_to(message, "❌ يجب إرسال ملف كـ Document أو إرسال النص (JSON) مباشرة.")
            
        parsed_data = json.loads(raw_data)
        save_series_data(parsed_data)
        bot.reply_to(message, "✅ <b>تمت الاستعادة بنجاح!</b>", parse_mode="HTML")
    except json.JSONDecodeError:
        bot.reply_to(message, "❌ <b>خطأ:</b> النص المرسل ليس بصيغة JSON صحيحة.", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ خطأ في الاستعادة: {e}")

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    threading.Thread(target=run_scheduler, daemon=True).start()
    while True:
        try: bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except: time.sleep(5)
