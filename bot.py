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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = "7808630939:AAET8hUTUOc82d_OdM0YTGm_5nRfG_GBFD8"
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "1013251619")

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DATA_FILE = os.path.join(DATA_DIR, "series.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

# تم تقليل الفحص لـ 40 ثانية بناءً على طلبك
CHECK_INTERVAL_SECONDS = 40
API_URL = "https://arabfleex.live/api_bot.php"
SECRET_KEY = "ArabFleex_2024_SecRet"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

scan_lock = threading.Lock()
last_scan_at = None
scan_cycles = 0
total_added = 0
bot_start_time = datetime.now(timezone.utc)

global_laroza_domain = "https://larooza.asia"
manual_domain = None

# قائمة سيرفرات الفيديو الحقيقية لفلترة الحلقات الوهمية
REAL_VIDEO_HOSTS = [
    "streamtape", "uqload", "doodstream", "dood.", "mp4upload",
    "voe.sx", "filemoon", "ok.ru", "vk.com", "youtube.com",
    "dailymotion", "upvid", "vidoza", "mixdrop", "upstream",
    "supervideo", "videobin", "fembed", "jawcloud", "jetload",
    "hxfile", "vtbe", "vidbom", "vidhide", "playtube",
    "waaw", "wolfstream", "embedgram", "gdriveplayer",
    "gofile", "streamlare", "streamvid", "tubeload",
    "okhd.site", "film77.xyz", "vidoba.org", "larhu.website",
    "mp4plus.org", "anafast.org", "vidspeed.org", "abstream.to",
    "dsvplay.com", "vidbam.org", "vidmoly.to", "streamwish",
    "embedwish", "filelions", "swiftplayer", "vidsrc",
    "turbobit", "nitroflare", "1fichier"
]

BANNED_SERVERS = ['fembed', 'nitro', 'arabseed', 'wecima']

LAROZA_KNOWN_DOMAINS = [
    "https://larooza.asia",
    "https://laroza.lat",
    "https://larozza.forum",
    "https://larozza.beer",
    "https://larroza.baby",
    "https://larroza.click",
    "https://larroza.casa",
]

def load_config():
    global global_laroza_domain, manual_domain
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                if "laroza_domain" in data:
                    global_laroza_domain = data["laroza_domain"]
                if "manual_domain" in data:
                    manual_domain = data["manual_domain"]
        except: pass

def save_config():
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump({
            "laroza_domain": global_laroza_domain,
            "manual_domain": manual_domain
        }, file, ensure_ascii=False)

load_config()

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

def is_real_server(url):
    if not url: return False
    url_lower = url.lower()
    for ban in BANNED_SERVERS:
        if ban in url_lower: return False
    return any(host in url_lower for host in REAL_VIDEO_HOSTS)

def extract_video_fingerprint(url):
    if not url: return None
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        parts = [p for p in parsed.path.strip('/').lower().split('/') if p]
        if len(parts) >= 2: return f"{netloc}/{parts[0]}/{parts[1]}"
        base = f"{netloc}/{parts[0]}" if parts else netloc
        if parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=False)
            id_params = ['id', 'oid', 'v', 'vid', 'file', 'i', 'key', 'uid', 'code']
            id_parts = []
            for p in id_params:
                if p in qs: id_parts.append(f"{p}={qs[p][0].lower()}")
            if id_parts: return f"{base}?{'&'.join(id_parts)}"
            first_key = sorted(qs.keys())[0]
            return f"{base}?{first_key}={qs[first_key][0][:40].lower()}"
        return base
    except: return None

def replace_domain(url, new_domain):
    if not url: return url
    p_url = urlparse(url)
    p_dom = urlparse(new_domain)
    return p_url._replace(scheme=p_dom.scheme, netloc=p_dom.netloc).geturl()

def get_working_laroza_domain():
    global global_laroza_domain, manual_domain
    
    def test_domain(d):
        try:
            res = curl_requests.get(d, impersonate="chrome", timeout=10, verify=False)
            if res.status_code == 200 and ("video" in res.text or "view-serie" in res.text or "SeasonsEpisodes" in res.text or "laroza" in res.text.lower()):
                return True
        except: pass
        return False

    # 1. اختبار الدومين اليدوي أولاً
    if manual_domain:
        if test_domain(manual_domain):
            return manual_domain

    # 2. اختبار الدومين العام الحالي
    if global_laroza_domain and test_domain(global_laroza_domain):
        return global_laroza_domain

    # 3. اختبار القائمة المحفوظة
    for domain in LAROZA_KNOWN_DOMAINS:
        if test_domain(domain):
            global_laroza_domain = domain
            save_config()
            return domain
            
    # 4. البحث في DuckDuckGo (الملاذ الأخير)
    search_queries = ["موقع لاروزا", "larozza site", "laroza مسلسلات"]
    exclude_domains = ["youtube.com", "dailymotion.com", "yandex.com", "facebook.com", "twitter.com", "laaroza-tv.com", "cima.laaroza", "larozaa.top"]
    search_keywords = ["larozaa", "larozza", "laroza", "larooza"]

    for query in search_queries:
        try:
            req = curl_requests.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
                timeout=15,
                impersonate="chrome",
                verify=False
            )
            soup = BeautifulSoup(req.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                parsed = urlparse(href)
                netloc = parsed.netloc.lower()
                if not netloc or not parsed.scheme.startswith('http'): continue
                if any(ex in netloc for ex in exclude_domains): continue

                if any(kw in netloc for kw in search_keywords):
                    candidate = f"{parsed.scheme}://{parsed.netloc}"
                    if test_domain(candidate):
                        global_laroza_domain = candidate
                        save_config()
                        return candidate
        except: pass
        
    return global_laroza_domain

def select_servers(watch_urls, down_urls):
    seen = set()
    w_pool = [x for x in watch_urls if is_real_server(x) and not (x in seen or seen.add(x))]
    seen = set()
    d_pool = [x for x in down_urls if not (x in seen or seen.add(x))]

    def pop_match(pool, keywords):
        for kw in keywords:
            for url in pool:
                if kw.lower() in url.lower():
                    pool.remove(url)
                    return url
        return ""

    w1 = pop_match(w_pool, ['vidspeed'])
    w2 = pop_match(w_pool, ['mp4plus'])
    w3 = pop_match(w_pool, ['uqload'])
    w4 = pop_match(w_pool, ['ok.ru', 'ok', 'vk.com', 'vk'])
    
    if not w1 and w_pool: w1 = w_pool.pop(0)
    if not w2 and w_pool: w2 = w_pool.pop(0)
    if not w3 and w_pool: w3 = w_pool.pop(0)
    if not w4 and w_pool: w4 = w_pool.pop(0)

    d1 = pop_match(d_pool, ['liiivideo', 'livideo'])
    if not d1: d1 = pop_match(d_pool, ['uqload'])
    d2 = pop_match(d_pool, ['1cloud'])
    
    if not d1 and d_pool: d1 = d_pool.pop(0)
    if not d2 and d_pool: d2 = d_pool.pop(0)

    return w1, w2, w3, w4, d1, d2

def get_laroza_ep(current_url, target_ep, seen_fps):
    html_content = fetch_html(current_url)
    if not html_content: return None
    soup = BeautifulSoup(html_content, 'html.parser')
    
    target_url = None
    active_div = None
    
    active_tab = soup.select_one('.SeasonsBoxUL li.active')
    if active_tab and active_tab.has_attr('data-serie'):
        active_serie = active_tab['data-serie']
        active_div = soup.select_one(f'.SeasonsEpisodes[data-serie="{active_serie}"]')

    if not active_div:
        for div in soup.select('.SeasonsEpisodes'):
            if 'display:none' not in div.get('style', '').replace(' ', ''):
                active_div = div
                break

    search_container = active_div if active_div else soup
    for a in search_container.select('a'):
        if 'video.php' not in a.get('href', ''): continue
        em = a.find('em')
        if em and em.text.strip() == str(target_ep):
            target_url = urljoin(current_url, a.get('href'))
            break

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
    if not target_url: return "WAITING"
    
    target_html = html_content if target_url == current_url else fetch_html(target_url)
    if not target_html: return "WAITING"
    
    vid = extract_vid(target_url)
    if not vid: return "WAITING"
    
    target_soup = BeautifulSoup(target_html, 'html.parser')
    watch_urls = [tag.get('data-embed-url') for tag in target_soup.select('[data-embed-url]') if tag.get('data-embed-url')]
    
    if not watch_urls:
        play_url = urljoin(target_url, f"/play.php?vid={vid}")
        play_html = fetch_html(play_url)
        play_soup = BeautifulSoup(play_html, 'html.parser')
        watch_urls = [tag.get('data-embed-url') for tag in play_soup.select('[data-embed-url]') if tag.get('data-embed-url')]
        
    dl_url = urljoin(target_url, f"/download.php?vid={vid}")
    dl_html = fetch_html(dl_url)
    dl_soup = BeautifulSoup(dl_html, 'html.parser')
    down_urls = [tag.get('data-download-url') for tag in dl_soup.select('[data-download-url]') if tag.get('data-download-url')]
        
    valid_watches = [u for u in watch_urls if is_real_server(u)]
    
    is_fake = False
    for link in valid_watches:
        fp = extract_video_fingerprint(link)
        if fp and (fp in seen_fps):
            is_fake = True
            break
            
    if not valid_watches:
        return "WAITING"
        
    if is_fake:
        return "FAKE"

    return {"url": target_url, "watch_urls": valid_watches, "down_urls": down_urls}

def scan_item(slug, info):
    global total_added
    target_ep = info["last_ep"] + 1
    
    if not info.get("laroza_url"): 
        info["status_text"] = "لا يوجد رابط لاروزا"
        return False
        
    active_domain = get_working_laroza_domain()
    current_url = replace_domain(info["laroza_url"], active_domain)
    
    if "laroza_seen_fps" not in info: info["laroza_seen_fps"] = []
    
    res = get_laroza_ep(current_url, target_ep, info["laroza_seen_fps"])
    
    if res == "WAITING":
        info["status_text"] = f"بانتظار ح{target_ep}"
        return False
    elif res == "FAKE":
        info["status_text"] = f"ح{target_ep} وهمية — بانتظار السيرفر الحقيقي"
        return False
    elif res and isinstance(res, dict):
        w1, w2, w3, w4, d1, d2 = select_servers(res["watch_urls"], res["down_urls"])
        
        payload = {
            "secret_key": SECRET_KEY, "action": "insert", "series_id": info["series_id"],
            "title": f"الحلقة {target_ep}", "episode_number": target_ep,
            "watch_link": w1, "watch_link_2": w2, "watch_link_3": w3, "watch_link_4": w4,
            "download_link": d1, "download_link_2": d2
        }
        
        try:
            api_res = requests.post(API_URL, data=payload, timeout=30, verify=False)
            api_text = api_res.text.strip()
            safe_api_status = api_text[:50].replace('<', '').replace('>', '')
            
            info["last_ep"] = target_ep
            info["laroza_url"] = res["url"]
            info["status_text"] = f"✅ تم اصطياد ح{target_ep}"
            
            # زيادة العداد عند نجاح الرفع فقط
            total_added += 1
            
            for u in res["watch_urls"]:
                fp = extract_video_fingerprint(u)
                if fp and fp not in info["laroza_seen_fps"]:
                    info["laroza_seen_fps"].append(fp)
            info["laroza_seen_fps"] = info["laroza_seen_fps"][-50:] 
            
            msg = f"🎬 <b>تم اصطياد وتحديث الحلقة {target_ep}</b>\n"
            msg += f"📺 <b>المسلسل:</b> {info['title']}\n"
            msg += f"🌐 <b>حالة الـ API:</b> <code>{safe_api_status}</code>\n"
            send_telegram_msg(msg)
            return True
            
        except Exception as e:
            info["status_text"] = f"خطأ اتصال بالـ API في ح{target_ep}"
            return False

    return False

def scan_all_series_once():
    global last_scan_at, scan_cycles
    if not scan_lock.acquire(blocking=False): return
    try:
        scan_cycles += 1
        last_scan_at = datetime.now(timezone.utc)
        
        # نأخذ قائمة المسلسلات فقط في البداية عشان نتجنب التداخل
        slugs = list(load_series_data().keys())
        
        for slug in slugs:
            # نحمل البيانات طازجة قبل فحص كل مسلسل
            data = load_series_data()
            if slug not in data: continue # لو اتمسح نتخطاه
            
            info = data[slug]
            ep_before_scan = info.get("last_ep")
            
            scan_item(slug, info)
            
            # نحمل البيانات تاني بعد الفحص (عشان لو تعدلت بـ setep واحنا بنفحص)
            latest_data = load_series_data()
            if slug in latest_data:
                # لو المستخدم عدل الحلقة يدوياً أثناء الفحص، متعملش حفظ عشان منمسحش تعديله
                if latest_data[slug].get("last_ep") != ep_before_scan:
                    continue 
                    
                latest_data[slug] = info
                save_series_data(latest_data)
                
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
    bot.reply_to(message, "🤖 <b>نظام المراقبة الذكي V2 (لاروزا)</b>\n\n🔹 <code>/add</code> — إضافة مسلسل\n🔹 <code>/del</code> — حذف مسلسل\n🔹 <code>/setep</code> — تعديل رقم آخر حلقة\n🔹 <code>/status</code> — تقرير الحالة الشامل\n🔹 <code>/setdomain</code> — تحديد دومين لاروزا يدوي 🔒\n🔹 <code>/cleardomain</code> — إلغاء الدومين اليدوي 🔓\n🔹 <code>/backup</code> — نسخة احتياطية\n🔹 <code>/restore</code> — استعادة البيانات", parse_mode="HTML")

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
    msg = bot.reply_to(message, "🔗 <b>أرسل رابط آخر حلقة من موقع لاروزا</b>\n(الرابط لازم يحتوي على video.php):", parse_mode="HTML")
    bot.register_next_step_handler(msg, add_step_laroza, title, series_id, last_ep)

def add_step_laroza(message, title, series_id, last_ep):
    laroza_url = message.text.strip()
    if "video.php" not in laroza_url:
        msg = bot.reply_to(message, "❌ الرابط لا يحتوي على video.php!\nأرسل رابط الحلقة الصحيح.", parse_mode="HTML")
        bot.register_next_step_handler(msg, add_step_laroza, title, series_id, last_ep)
        return
        
    laroza_seen_fps = []
    bot.send_message(message.chat.id, "⏳ جاري حفظ بصمات لاروزا الحالية للحماية وتثبيت الدومين...")
    
    parsed = urlparse(laroza_url)
    new_dom = f"{parsed.scheme}://{parsed.netloc}"
    if not manual_domain:
        global global_laroza_domain
        global_laroza_domain = new_dom
        save_config()

    try:
        qs = parse_qs(urlparse(laroza_url).query)
        vid = qs.get('vid', [None])[0]
        if vid:
            play_url = urljoin(laroza_url, f"/play.php?vid={vid}")
            play_html = fetch_html(play_url)
            play_soup = BeautifulSoup(play_html, 'html.parser')
            w_urls = [li.get('data-embed-url') for li in play_soup.select('[data-embed-url]') if li.get('data-embed-url')]
            for u in w_urls:
                if is_real_server(u):
                    fp = extract_video_fingerprint(u)
                    if fp: laroza_seen_fps.append(fp)
    except: pass

    slug = f"series_{series_id}"
    data = load_series_data()
    data[slug] = {
        "title": title, "series_id": series_id, "last_ep": last_ep,
        "laroza_url": laroza_url, "laroza_seen_fps": laroza_seen_fps,
        "status_text": "بانتظار الفحص"
    }
    save_series_data(data)
    bot.reply_to(message, f"✅ تمت إضافة المسلسل بنجاح!\nالبوت مستعد لمراقبة حلقة {last_ep + 1}.", parse_mode="HTML")

@bot.message_handler(commands=["setdomain"])
def set_domain_cmd(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    msg = bot.reply_to(message, "🔗 <b>أرسل رابط الدومين اليدوي الجديد لموقع لاروزا:</b>\n(مثال: https://larooza.asia)", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_setdomain)

def process_setdomain(message):
    new_url = message.text.strip().rstrip('/')
    if not new_url.startswith("http"): new_url = "https://" + new_url
    
    global manual_domain
    manual_domain = new_url
    save_config()
    bot.reply_to(message, f"✅ <b>تم تثبيت الدومين اليدوي بنجاح:</b>\n<code>{new_url}</code>\n\nالبوت هيعتمد عليه كأولوية قصوى. لإلغائه استخدم /cleardomain", parse_mode="HTML")

@bot.message_handler(commands=["cleardomain"])
def clear_domain_cmd(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    global manual_domain
    manual_domain = None
    save_config()
    bot.reply_to(message, "🔓 <b>تم إلغاء الدومين اليدوي.</b>\nالبوت الآن سيعتمد على الاكتشاف التلقائي للدومين الشغال.", parse_mode="HTML")

@bot.message_handler(commands=["status", "list"])
def list_items(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    data = load_series_data()
    
    now = datetime.now(timezone.utc)
    uptime_seconds = (now - bot_start_time).total_seconds()
    h = int(uptime_seconds // 3600)
    m = int((uptime_seconds % 3600) // 60)
    sec = int(uptime_seconds % 60)
    
    start_str = bot_start_time.strftime("%Y-%m-%d %H:%M:%S")
    last_scan_str = last_scan_at.strftime("%Y-%m-%d %H:%M:%S") if last_scan_at else "لم يبدأ بعد"
    
    current_dom = get_working_laroza_domain()
    dom_type = " (يدوي 🔒)" if manual_domain else " (تلقائي 🔄)"
    
    msg_text = f"✅ <b>البوت شغّال وتمام!</b>\n\n"
    msg_text += f"⏱ <b>وقت التشغيل:</b> {h}س {m}د {sec}ث\n"
    msg_text += f"🕐 <b>بدأ في:</b> {start_str}\n"
    msg_text += f"🔍 <b>آخر فحص:</b> {last_scan_str}\n"
    msg_text += f"🌐 <b>الدومين الحالي:</b> {current_dom}{dom_type}\n"
    msg_text += f"🔄 <b>عدد دورات الفحص:</b> {scan_cycles}\n"
    msg_text += f"➕ <b>إجمالي الحلقات المضافة:</b> {total_added}\n\n"
    msg_text += f"📺 <b>آخر حالة للمسلسلات:</b>\n\n"

    if not data:
        msg_text += "📭 لا توجد مسلسلات مضافة."
    else:
        for slug, info in data.items():
            title = str(info.get("title", slug))
            last_ep = info.get("last_ep", 0)
            status_txt = info.get("status_text", "بانتظار الفحص")
            msg_text += f"🔹 <b>{title}:</b> حلقة {last_ep} — <i>{status_txt}</i>\n"
            
    bot.reply_to(message, msg_text, parse_mode="HTML")

@bot.message_handler(commands=["setep"])
def set_ep_start(message):
    if str(message.chat.id) != ADMIN_CHAT_ID: return
    data = load_series_data()
    if not data: return bot.reply_to(message, "📭 القائمة فارغة.")
    markup = InlineKeyboardMarkup(row_width=1)
    for slug, info in data.items():
        title = info.get('title', slug)
        markup.add(InlineKeyboardButton(text=f"✏️ تعديل: {title}", callback_data=f"setep_{slug}"))
    bot.reply_to(message, "🔢 اختر المسلسل لتعديل رقم آخر حلقة مسجلة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('setep_'))
def process_setep_callback(call):
    slug = call.data.split('setep_')[1]
    msg = bot.send_message(call.message.chat.id, "🔢 أرسل رقم آخر حلقة صحيحة للمسلسل الآن:\n(مثال: لو عاوز البوت يبحث عن 88، اكتب هنا 87)")
    bot.register_next_step_handler(msg, save_new_ep, slug)
    bot.answer_callback_query(call.id)

def save_new_ep(message, slug):
    try: new_ep = int(message.text.strip())
    except: return bot.reply_to(message, "❌ يجب أن يكون رقماً.")
    data = load_series_data()
    if slug in data:
        data[slug]['last_ep'] = new_ep
        data[slug]['status_text'] = "تم التعديل اليدوي، جاري الفحص..."
        data[slug]['laroza_seen_fps'] = [] # تصفير البصمات عشان ميفتكرش الحلقة الجديدة مكررة
        save_series_data(data)
        bot.reply_to(message, f"✅ تم تعديل آخر حلقة للمسلسل لتكون {new_ep} بنجاح.\nالبوت الآن سيبحث عن الحلقة {new_ep + 1}.")
    else:
        bot.reply_to(message, "❌ المسلسل غير موجود.")

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
            return bot.reply_to(message, "❌ يجب إرسال ملف أو نص.")
            
        parsed_data = json.loads(raw_data.strip())
        
        for slug in list(parsed_data.keys()):
            if "tracking" in parsed_data[slug]: del parsed_data[slug]["tracking"]
            if "qdrama_url" in parsed_data[slug]: del parsed_data[slug]["qdrama_url"]
            parsed_data[slug]["status_text"] = "تمت الاستعادة بنجاح، جاري الفحص..."
            
        save_series_data(parsed_data)
        bot.reply_to(message, "✅ <b>تمت الاستعادة وتحديث هيكل البيانات بنجاح!</b>", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ خطأ في الاستعادة: {e}")

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    threading.Thread(target=run_scheduler, daemon=True).start()
    print("Bot is running...")
    while True:
        try: bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except: time.sleep(5)
