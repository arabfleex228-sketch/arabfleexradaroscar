import html
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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = "7808630939:AAEY0_q6vnkKlMRjvXNmEXwK1G80hv0vghY"
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "1013251619")

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DATA_FILE = os.path.join(DATA_DIR, "series.json")

# الفحص كل 5 دقائق
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
# أقصى مدة لمراقبة الحلقة لتجميع المصدرين (ساعة)
MAX_TRACKING_TIME_SECONDS = 3600 

API_URL = "https://arabfleex.live/api_bot.php"
SECRET_KEY = "ArabFleex_2024_SecRet"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

scan_lock = threading.Lock()
started_at = datetime.now(timezone.utc)
last_scan_at = None
scan_cycles = 0
total_added = 0
last_scan_result = "لم يبدأ فحص بعد"

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
        # استخدام curl_cffi لتخطي حمايات Cloudflare بسهولة
        res = curl_requests.get(url, impersonate="chrome", timeout=15, verify=False)
        return res.text
    except: return ""

def extract_vid(url):
    qs = parse_qs(urlparse(url).query)
    return qs.get('vid', [None])[0]

def is_time_to_scan(info):
    release_time_str = info.get("release_time")
    if not release_time_str: return True 
    try:
        release_time_str = release_time_str.strip().upper()
        if ":" in release_time_str: dt = datetime.strptime(release_time_str, "%I:%M %p")
        else: dt = datetime.strptime(release_time_str, "%I %p")
    except: return True

    egypt_tz = timezone(timedelta(hours=3))
    now = datetime.now(egypt_tz)
    now_mins = now.hour * 60 + now.minute
    target_mins = dt.hour * 60 + dt.minute
    diff = now_mins - target_mins
    if diff < -720: diff += 1440
    elif diff > 720: diff -= 1440
    
    if -60 <= diff <= 600: return True
    return False

def get_laroza_ep(current_url, target_ep):
    html = fetch_html(current_url)
    if not html: return None
    soup = BeautifulSoup(html, 'html.parser')
    
    target_url = None
    
    # 1. فحص أزرار الحلقات
    for a in soup.select('.SeasonsEpisodes a'):
        em = a.find('em')
        if em and em.text.strip() == str(target_ep):
            target_url = urljoin(current_url, a.get('href'))
            break

    # 2. فحص قائمة Select
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
    
    # لو ملقاش الحلقة المطلوبة، بس الرابط الحالي هو نفس الحلقة المطلوبة (في حالة الفحص الأولي لمنع الحلقات الوهمية)
    if not target_url and current_vid and "video.php" in current_url:
        target_url = current_url
                
    if not target_url: return None
    vid = extract_vid(target_url)
    if not vid: return None
    
    # جلب سيرفرات المشاهدة
    play_url = urljoin(target_url, f"/play.php?vid={vid}")
    play_html = fetch_html(play_url)
    play_soup = BeautifulSoup(play_html, 'html.parser')
    watch_urls = []
    for li in play_soup.select('ul.WatchList li'):
        u = li.get('data-embed-url')
        if u: watch_urls.append(u)
        
    # جلب سيرفرات التحميل
    dl_url = urljoin(target_url, f"/download.php?vid={vid}")
    dl_html = fetch_html(dl_url)
    dl_soup = BeautifulSoup(dl_html, 'html.parser')
    down_urls = []
    for li in dl_soup.select('ul.downloadlist li'):
        u = li.get('data-download-url')
        if u: down_urls.append(u)
        
    return {"url": target_url, "watch_urls": watch_urls, "down_urls": down_urls}

def get_qdrama_ep(current_url, target_ep):
    html = fetch_html(current_url)
    if not html: return None
    soup = BeautifulSoup(html, 'html.parser')
    
    target_url = None
    for a in soup.select('.AiredEPS a'):
        em = a.find('em')
        if em and em.text.strip() == str(target_ep):
            target_url = urljoin(current_url, a.get('href'))
            break
            
    qs = parse_qs(urlparse(current_url).query)
    current_vid = qs.get('vid', [None])[0]
    
    # في حالة الفحص الأولي لمنع الحلقات الوهمية
    if not target_url and current_vid and "watch.php" in current_url:
        target_url = current_url
            
    if not target_url: return None
    vid = extract_vid(target_url)
    if not vid: return None
    
    # جلب سيرفرات المشاهدة
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
            
    # جلب سيرفرات التحميل
    dl_url = urljoin(target_url, f"/download.php?vid={vid}")
    dl_html = fetch_html(dl_url)
    dl_soup = BeautifulSoup(dl_html, 'html.parser')
    down_urls = []
    for a in dl_soup.select('.download-servers-container a.download-btn, .special-download a.special-btn'):
        u = a.get('href')
        if u: down_urls.append(u)
        
    return {"url": target_url, "watch_urls": watch_urls, "down_urls": down_urls}

def select_servers(watch_urls, down_urls):
    # إزالة التكرار مع الحفاظ على الترتيب التقريبي
    seen = set()
    w_pool = [x for x in watch_urls if not (x in seen or seen.add(x))]
    seen = set()
    d_pool = [x for x in down_urls if not (x in seen or seen.add(x))]

    def pop_match(pool, keywords):
        for kw in keywords:
            for url in pool:
                if kw.lower() in url.lower():
                    pool.remove(url)
                    return url
        if pool: return pool.pop(0)
        return ""

    # توزيع سيرفرات المشاهدة الـ 4 حسب الأولوية
    w1 = pop_match(w_pool, ['uqload', 'liiivideo', 'okhd'])
    w2 = pop_match(w_pool, ['vidspeed'])
    w3 = pop_match(w_pool, ['rty', 'ok.ru', 'vk.com', 'anafast', 'vidmoly'])
    w4 = pop_match(w_pool, ['rty', 'ok.ru', 'vk.com', 'anafast', 'vidmoly'])
    
    # سد الخانات الفارغة بأي سيرفرات مشاهدة متبقية
    if not w1 and w_pool: w1 = w_pool.pop(0)
    if not w2 and w_pool: w2 = w_pool.pop(0)
    if not w3 and w_pool: w3 = w_pool.pop(0)
    if not w4 and w_pool: w4 = w_pool.pop(0)

    # توزيع سيرفرات التحميل الـ 2 حسب الأولوية
    d1 = pop_match(d_pool, ['1cloud', 'liiivideo'])
    d2 = pop_match(d_pool, ['voe', 'uqload'])
    
    # سد الخانات الفارغة بأي سيرفرات تحميل متبقية
    if not d1 and d_pool: d1 = d_pool.pop(0)
    if not d2 and d_pool: d2 = d_pool.pop(0)

    return w1, w2, w3, w4, d1, d2

def scan_item(slug, info, is_manual=False):
    global last_scan_result
    
    if not is_manual and "tracking" not in info and not is_time_to_scan(info):
        last_scan_result = f"{info.get('title', slug)}: خارج موعد النزول 💤"
        return False
        
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
    
    # 1. فحص لاروزا
    if info.get("laroza_url") and not tracking["laroza_done"]:
        res = get_laroza_ep(info["laroza_url"], target_ep)
        if res and res["watch_urls"]:
            # فحص الحلقة الوهمية (إذا كانت سيرفرات الحلقة الجديدة مطابقة للحلقة القديمة)
            if set(res["watch_urls"]) == set(info.get("laroza_last_servers", [])):
                pass # حلقة وهمية
            else:
                tracking["watch_urls"].extend(res["watch_urls"])
                tracking["down_urls"].extend(res["down_urls"])
                tracking["laroza_done"] = True
                tracking["laroza_new_url"] = res["url"]
                info["laroza_last_servers"] = res["watch_urls"]
                new_discovery = True

    # 2. فحص كيو دراما
    if info.get("qdrama_url") and not tracking["qdrama_done"]:
        res = get_qdrama_ep(info["qdrama_url"], target_ep)
        if res and res["watch_urls"]:
            # فحص الحلقة الوهمية
            if set(res["watch_urls"]) == set(info.get("qdrama_last_servers", [])):
                pass # حلقة وهمية
            else:
                tracking["watch_urls"].extend(res["watch_urls"])
                tracking["down_urls"].extend(res["down_urls"])
                tracking["qdrama_done"] = True
                tracking["qdrama_new_url"] = res["url"]
                info["qdrama_last_servers"] = res["watch_urls"]
                new_discovery = True
                
    # 3. إرسال البيانات للـ API في حالة اكتشاف جديد
    if new_discovery:
        if tracking["status"] == "waiting":
            action = "insert"
            tracking["status"] = "partial"
            tracking["start_ts"] = time.time() # بدء مؤقت الساعة لانتظار الموقع الآخر
        else:
            action = "update"
            
        w1, w2, w3, w4, d1, d2 = select_servers(tracking["watch_urls"], tracking["down_urls"])
        
        payload = {
            "secret_key": SECRET_KEY, "action": action, "series_id": info["series_id"],
            "title": f"الحلقة {target_ep}", "episode_number": target_ep,
            "watch_link": w1, "watch_link_2": w2, "watch_link_3": w3, "watch_link_4": w4,
            "download_link": d1, "download_link_2": d2
        }
        
        try:
            api_res = requests.post(API_URL, data=payload, timeout=30, verify=False)
            safe_api_status = html.escape(api_res.text[:50])
        except Exception as e:
            safe_api_status = f"خطأ اتصال: {str(e)[:30]}"
            
        msg = f"🎬 <b>تم اصطياد وتحديث الحلقة {target_ep}</b>\n"
        msg += f"📺 <b>المسلسل:</b> {info['title']}\n"
        msg += f"📡 <b>المصادر:</b> "
        sources = []
        if tracking["laroza_done"]: sources.append("لاروزا ✅")
        if tracking["qdrama_done"]: sources.append("كيو دراما ✅")
        msg += " | ".join(sources) + "\n"
        msg += f"🌐 <b>حالة الـ API:</b> <code>{safe_api_status}</code>\n\n"
        
        if tracking["status"] == "partial":
            msg += f"⏳ <i>في انتظار المصدر الآخر لاكتمال التجميع...</i>"
        else:
            msg += f"🔥 <i>تم اكتمال التجميع من المصادر المتاحة!</i>"
            
        send_telegram_msg(msg)

    # 4. فحص إنهاء المراقبة
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

def scan_all_series_once(is_manual=False):
    global last_scan_at, scan_cycles, total_added
    if not scan_lock.acquire(blocking=False): return []
    try:
        scan_cycles += 1
        last_scan_at = datetime.now(timezone.utc)
        data = load_series_data()
        results = []
        for slug, info in list(data.items()):
            if scan_item(slug, info, is_manual=is_manual):
                total_added += 1
                save_series_data(data)
                results.append(f"{info.get('title', slug)}: فحص نشط 🔄")
        return results
    except Exception as e:
        print(f"[ERROR] Error during full scan: {e}")
        return []
    finally:
        scan_lock.release()

schedule.every(CHECK_INTERVAL_SECONDS).seconds.do(lambda: scan_all_series_once(False))

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

# دالة لتسجيل سيرفرات الحلقة الحالية لمنع الحلقات الوهمية
def cache_initial_servers(slug, info):
    bot.send_message(ADMIN_CHAT_ID, f"⏳ جاري حفظ سيرفرات حلقة {info['last_ep']} للمسلسل [{info['title']}] في الخلفية (لمنع الحلقات الوهمية)...")
    updated = False
    
    if info.get('laroza_url') and not info.get('laroza_last_servers'):
        res = get_laroza_ep(info['laroza_url'], info['last_ep'])
        if res and res["watch_urls"]:
            info['laroza_last_servers'] = res["watch_urls"]
            info['laroza_url'] = res["url"] # تحديث الرابط لضمان دقته
            updated = True
            
    if info.get('qdrama_url') and not info.get('qdrama_last_servers'):
        res = get_qdrama_ep(info['qdrama_url'], info['last_ep'])
        if res and res["watch_urls"]:
            info['qdrama_last_servers'] = res["watch_urls"]
            info['qdrama_url'] = res["url"]
            updated = True
            
    if updated:
        data = load_series_data()
        if slug in data:
            data[slug]['laroza_last_servers'] = info.get('laroza_last_servers', [])
            data[slug]['laroza_url'] = info.get('laroza_url', '')
            data[slug]['qdrama_last_servers'] = info.get('qdrama_last_servers', [])
            data[slug]['qdrama_url'] = info.get('qdrama_url', '')
            save_series_data(data)
        bot.send_message(ADMIN_CHAT_ID, f"✅ تم الانتهاء من تخزين سيرفرات [{info['title']}] بنجاح! البوت مستعد لمراقبة حلقة {info['last_ep']+1}.")
    else:
        bot.send_message(ADMIN_CHAT_ID, f"⚠️ لم أتمكن من جلب سيرفرات الحلقة الحالية لـ [{info['title']}]. يرجى التأكد من الروابط.")

def admin_only(message):
    return str(message.chat.id) == str(ADMIN_CHAT_ID)

@bot.message_handler(commands=["start", "help"])
def welcome(message):
    if admin_only(message):
        bot.reply_to(message, "🤖 <b>نظام المراقبة (لاروزا + كيو دراما)</b>\n\n🔹 <code>/add</code> — إضافة مسلسل جديد\n🔹 <code>/del</code> — حذف مسلسل\n🔹 <code>/list</code> — قائمة المسلسلات وحالتها\n🔹 <code>/settime</code> — تحديد موعد النزول ⏱\n🔹 <code>/scan</code> — فحص يدوي سريع 🚀", parse_mode="HTML")

@bot.message_handler(commands=["add"])
def add_item_start(message):
    if not admin_only(message): return
    msg = bot.reply_to(message, "📝 <b>أرسل اسم المسلسل (عربي أو إنجليزي):</b>", parse_mode="HTML")
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
    msg = bot.reply_to(message, "🔗 <b>أرسل رابط آخر حلقة من موقع لاروزا</b>\n(الرابط لازم يكون لصفحة فيديو الحلقة <code>video.php</code> وليس المسلسل)\nلو مش هتراقب لاروزا للمسلسل ده، اكتب <code>تخطي</code>:", parse_mode="HTML")
    bot.register_next_step_handler(msg, add_step_laroza, title, series_id, last_ep)

def add_step_laroza(message, title, series_id, last_ep):
    laroza_url = message.text.strip()
    # التأكد من إدخال رابط حلقة صحيح
    if laroza_url != "تخطي" and "video.php" not in laroza_url:
        msg = bot.reply_to(message, "❌ الرابط ده بتاع المسلسل نفسه مش الحلقة!\nأرجوك افتح صفحة <b>آخر حلقة</b> (اللي بيكون فيها المشاهدة واسمها video.php) وابعتهالي تاني\nأو اكتب <code>تخطي</code>", parse_mode="HTML")
        bot.register_next_step_handler(msg, add_step_laroza, title, series_id, last_ep)
        return
        
    if laroza_url == "تخطي": laroza_url = ""
    msg = bot.reply_to(message, "🔗 <b>أرسل رابط آخر حلقة من موقع كيو دراما</b>\n(الرابط لازم يكون لصفحة الحلقة <code>watch.php</code>)\nلو مش هتراقب كيو دراما، اكتب <code>تخطي</code>:", parse_mode="HTML")
    bot.register_next_step_handler(msg, add_step_qdrama, title, series_id, last_ep, laroza_url)

def add_step_qdrama(message, title, series_id, last_ep, laroza_url):
    qdrama_url = message.text.strip()
    
    if qdrama_url != "تخطي" and "watch.php" not in qdrama_url:
        msg = bot.reply_to(message, "❌ الرابط ده بتاع المسلسل نفسه مش الحلقة!\nأرجوك افتح صفحة <b>آخر حلقة</b> (اللي بيكون واسمها watch.php) وابعتهالي تاني\nأو اكتب <code>تخطي</code>", parse_mode="HTML")
        bot.register_next_step_handler(msg, add_step_qdrama, title, series_id, last_ep, laroza_url)
        return
        
    if qdrama_url == "تخطي": qdrama_url = ""
    
    if not laroza_url and not qdrama_url:
        return bot.reply_to(message, "❌ لازم تحط رابط لموقع واحد على الأقل!")
        
    slug = f"series_{series_id}"
    data = load_series_data()
    data[slug] = {
        "title": title,
        "series_id": series_id,
        "last_ep": last_ep,
        "laroza_url": laroza_url,
        "qdrama_url": qdrama_url,
        "laroza_last_servers": [],
        "qdrama_last_servers": []
    }
    save_series_data(data)
    bot.reply_to(message, "✅ تمت إضافة المسلسل بنجاح!", parse_mode="HTML")
    threading.Thread(target=cache_initial_servers, args=(slug, data[slug]), daemon=True).start()

@bot.message_handler(commands=["list", "status", "check"])
def list_items(message):
    if not admin_only(message): return
    uptime = (datetime.now(timezone.utc) - started_at).total_seconds()
    data = load_series_data()
    lines = [
        "✅ <b>البوت شغال وبيفحص بانتظام!</b>\n",
        f"🔍 <b>آخر فحص:</b> {last_scan_at.astimezone().strftime('%Y-%m-%d %H:%M:%S') if last_scan_at else 'لم يبدأ'}",
        f"🔄 <b>دورات الفحص:</b> {scan_cycles} | ➕ <b>تحديثات:</b> {total_added}\n"
    ]
    if not data: lines.append("📭 لا توجد عناصر مضافة.")
    else:
        for slug, info in data.items():
            title = html.escape(str(info.get("title", slug)))
            last_ep = info.get("last_ep", 0)
            status_text = "مستعد ✅"
            if "tracking" in info:
                tr = info["tracking"]
                if tr["status"] == "waiting": status_text = f"⏳ بانتظار حلقة {tr['episode']}"
                elif tr["status"] == "partial":
                    done = []
                    if tr["laroza_done"]: done.append("لاروزا")
                    if tr["qdrama_done"]: done.append("كيو")
                    status_text = f"🔄 بانتظار الباقي (تم: {'+'.join(done)})"
            lines.append(f"🎬 <b>{title}</b> (حلقة {last_ep}) | {status_text}")
            
    bot.reply_to(message, "\n".join(lines), parse_mode="HTML")

@bot.message_handler(commands=["del"])
def delete_item(message):
    if not admin_only(message): return
    data = load_series_data()
    markup = InlineKeyboardMarkup(row_width=1)
    for slug, info in data.items():
        markup.add(InlineKeyboardButton(text=f"❌ حذف: {info.get('title', slug)}", callback_data=f"del_{slug}"))
    if not data: return bot.reply_to(message, "📭 القائمة فارغة.")
    bot.reply_to(message, "🗑 اختر المسلسل للحذف:", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_'))
def process_delete_callback(call):
    slug = call.data.split('del_')[1]
    data = load_series_data()
    if slug in data:
        del data[slug]
        save_series_data(data)
        bot.answer_callback_query(call.id, "تم الحذف بنجاح!")
        bot.edit_message_text("✅ تم الحذف.", call.message.chat.id, call.message.message_id)

@bot.message_handler(commands=["settime"])
def set_release_time(message):
    if not admin_only(message): return
    try:
        parts = message.text.split(" ", 2)
        series_id = parts[1]
        time_str = parts[2].strip().upper()
        slug = f"series_{series_id}"
        
        data = load_series_data()
        if slug in data:
            data[slug]["release_time"] = time_str
            save_series_data(data)
            bot.reply_to(message, f"✅ تم تحديد موعد نزول [{data[slug]['title']}] الساعة: {time_str}\n💤 البوت هينام ويصحى يراقبه قبل الميعاد.", parse_mode="HTML")
        else:
            bot.reply_to(message, "❌ ID المسلسل غير موجود.")
    except:
        bot.reply_to(message, "❌ الصيغة: /settime ID 8:00 PM")

@bot.message_handler(commands=["scan"])
def force_check(message):
    if not admin_only(message): return
    bot.reply_to(message, "🔎 <b>بدأ الفحص السريع اليدوي... 🚀</b>", parse_mode="HTML")
    def background_scan():
        results = scan_all_series_once(is_manual=True)
        if results:
            try: bot.send_message(message.chat.id, "✅ <b>تم الفحص!</b>\n\n" + ("\n".join(results)), parse_mode="HTML")
            except: pass
    threading.Thread(target=background_scan, daemon=True).start()

if __name__ == "__main__":
    print("Bot is starting...", flush=True)
    try:
        bot.remove_webhook()
        time.sleep(1)
    except: pass
    
    os.makedirs(DATA_DIR, exist_ok=True)
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    print(f"Bot is running. Data will be saved in: {DATA_FILE}", flush=True)
    while True:
        try: bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"[ERROR] Polling crashed: {e}. Restarting...", flush=True)
            time.sleep(5)
