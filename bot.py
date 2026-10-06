import telebot
import requests
import time
import json
import os
import re
import threading
import schedule
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

TOKEN = "7808630939:AAEY0_q6vnkKlMRjvXNmEXwK1G80hv0vghY"
ADMIN_ID = 1013251619
API_URL = "https://arabfleex.xo.je/api.php"
SECRET_KEY = "ArabFleex_2024_SecRet"

CHECK_INTERVAL_SECONDS = 150
DATA_FILE = "series.json"

bot = telebot.TeleBot(TOKEN)

started_at = datetime.now(timezone.utc)
last_scan_at = None
scan_cycles = 0
total_added = 0
last_scan_result = "لم يبدأ فحص بعد"

BANNED_SERVERS = ["fembed", "nitro", "streamtape", "arabseed"]
LAROZA_KNOWN_DOMAINS = [
    "https://llaroza.surf",
    "https://larooza.asia",
    "https://laroza.lat",
    "https://larozza.forum",
    "https://larozza.beer",
    "https://larroza.baby",
    "https://larroza.click",
    "https://larroza.casa"
]

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def is_valid_url(url):
    if not url: return False
    url_lower = url.lower()
    for banned in BANNED_SERVERS:
        if banned in url_lower:
            return False
    return True

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
            qs = parse_qs(parsed.query)
            id_params = ['id', 'oid', 'v', 'vid', 'file', 'i', 'key', 'uid', 'code']
            id_parts = [f"{p}={qs[p][0].lower()}" for p in id_params if p in qs]
            if id_parts: return f"{base}?{'&'.join(id_parts)}"
            first_key = sorted(qs.keys())[0]
            return f"{base}?{first_key}={qs[first_key][0][:40].lower()}"
        return base
    except:
        return None

def select_servers(watch_urls, down_urls, source=""):
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

    if source == "laroza":
        w1 = pop_match(w_pool, ['vidspeed'])
        if not w1: w1 = pop_match(w_pool, ['uqload'])
        w2 = pop_match(w_pool, ['uqload']) 
        if not w2: w2 = pop_match(w_pool, ['liiivideo', 'livideo'])
    else:
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

    d1 = pop_match(d_pool, ['liiivideo', 'livideo'])
    if not d1: d1 = pop_match(d_pool, ['uqload'])
    d2 = pop_match(d_pool, ['uqload'])
    if not d2: d2 = pop_match(d_pool, ['1cloud', 'voe'])
    
    if not d1 and d_pool: d1 = d_pool.pop(0)
    if not d2 and d_pool: d2 = d_pool.pop(0)

    return w1, w2, w3, w4, d1, d2

def get_working_laroza_url(url):
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return url
    except:
        pass
    parsed = urlparse(url)
    for domain in LAROZA_KNOWN_DOMAINS:
        try:
            test_url = f"{domain}{parsed.path}?{parsed.query}"
            res = requests.get(test_url, timeout=10)
            if res.status_code == 200:
                return test_url
        except:
            continue
    return url

def get_laroza_ep(url, target_ep, seen_fps=None):
    if seen_fps is None: seen_fps = []
    url = get_working_laroza_url(url)
    try:
        res = requests.get(url, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        active_season_div = None
        for div in soup.find_all('div', class_='SeasonsEpisodes'):
            style = div.get('style', '').replace(' ', '').lower()
            if 'display:none' not in style:
                active_season_div = div
                break
        
        if not active_season_div:
            active_season_div = soup
            
        ep_link = None
        for a in active_season_div.find_all('a'):
            em = a.find('em')
            if em and em.text.strip() == str(target_ep):
                ep_link = a.get('href')
                break
                
        if not ep_link: return None
        
        parsed_base = urlparse(url)
        base_url = f"{parsed_base.scheme}://{parsed_base.netloc}"
        if not ep_link.startswith('http'):
            ep_link = f"{base_url}/{ep_link.lstrip('./')}"
            
        res_ep = requests.get(ep_link, timeout=15)
        soup_ep = BeautifulSoup(res_ep.text, 'html.parser')
        
        watch_urls = []
        down_urls = []
        new_fps = []
        
        for li in soup_ep.find_all('li', attrs={"data-embed-url": True}):
            src = li.get('data-embed-url', '')
            if src.startswith("//"): src = "https:" + src
            if is_valid_url(src): watch_urls.append(src)
            
        for iframe in soup_ep.find_all('iframe'):
            src = iframe.get('src', '')
            if src.startswith("//"): src = "https:" + src
            if is_valid_url(src) and src not in watch_urls:
                watch_urls.append(src)

        download_list = soup_ep.find('ul', class_='downloadlist')
        if download_list:
            for a in download_list.find_all('a'):
                href = a.get('href')
                if href and is_valid_url(href):
                    down_urls.append(href)
                    
        if not down_urls:
            return None 
            
        for w in watch_urls:
            fp = extract_video_fingerprint(w)
            if fp:
                if fp in seen_fps:
                    return None 
                new_fps.append(fp)

        return {"watch_urls": watch_urls, "down_urls": down_urls, "url": ep_link, "new_fps": new_fps}
    except Exception as e:
        print(f"Laroza Error: {e}")
        return None

def get_qdrama_ep(url, target_ep):
    try:
        res = requests.get(url, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        ep_link = None
        for a in soup.find_all('a', class_='AiredEPS'):
            ep_text = a.text.strip()
            num_match = re.search(r'\d+', ep_text)
            if num_match and num_match.group() == str(target_ep):
                ep_link = a.get('href')
                break
                
        if not ep_link: return None
        
        res_ep = requests.get(ep_link, timeout=15)
        soup_ep = BeautifulSoup(res_ep.text, 'html.parser')
        
        watch_urls = []
        down_urls = []
        
        for iframe in soup_ep.find_all('iframe'):
            src = iframe.get('src')
            if src and "youtube" not in src.lower() and is_valid_url(src):
                watch_urls.append(src)
                
        dl_div = soup_ep.find('div', class_='DownloadLinks')
        if dl_div:
            for a in dl_div.find_all('a'):
                href = a.get('href')
                if href and is_valid_url(href):
                    down_urls.append(href)
                    
        if not down_urls:
            return None
            
        return {"watch_urls": watch_urls, "down_urls": down_urls, "url": ep_link}
    except Exception as e:
        print(f"Qdrama Error: {e}")
        return None

def check_new_episodes():
    global scan_cycles, last_scan_at, total_added
    scan_cycles += 1
    last_scan_at = datetime.now(timezone.utc)
    
    data = load_data()
    changed = False
    
    for key, info in data.items():
        if info["status"] != "waiting": continue
        
        target_ep = info["current_episode"] + 1
        new_discovery = False
        
        if "laroza_seen_fps" not in info:
            info["laroza_seen_fps"] = []

        if info.get("laroza_url") and not info["laroza_done"]:
            res = get_laroza_ep(info["laroza_url"], target_ep, info["laroza_seen_fps"])
            if res:
                info["watch_urls"].extend(res["watch_urls"])
                info["down_urls"].extend(res["down_urls"])
                info["laroza_seen_fps"].extend(res["new_fps"]) 
                info["laroza_done"] = True
                info["laroza_new_url"] = res["url"]
                new_discovery = True
                
        if info.get("qdrama_url") and not info["qdrama_done"]:
            res = get_qdrama_ep(info["qdrama_url"], target_ep)
            if res:
                info["watch_urls"].extend(res["watch_urls"])
                info["down_urls"].extend(res["down_urls"])
                info["qdrama_done"] = True
                info["qdrama_new_url"] = res["url"]
                new_discovery = True
                    
        if new_discovery:
            action = "insert" if info["status"] == "waiting" else "update"
            info["status"] = "partial"
            
            source = "qdrama" if info["qdrama_done"] else "laroza"
            w1, w2, w3, w4, d1, d2 = select_servers(info["watch_urls"], info["down_urls"], source)
            
            payload = {
                "secret_key": SECRET_KEY, 
                "action": action, 
                "series_id": info["series_id"],
                "episode_number": target_ep,
                "title": f"الحلقة {target_ep}",
                "watch_link": w1, "watch_link_2": w2, "watch_link_3": w3, "watch_link_4": w4,
                "download_link": d1, "download_link_2": d2
            }
            
            try:
                r = requests.post(API_URL, data=payload, timeout=10)
                if "INSERTED" in r.text or "UPDATED" in r.text:
                    total_added += 1
                    source_str = []
                    if info["laroza_done"]: source_str.append("لاروزا")
                    if info["qdrama_done"]: source_str.append("كيو دراما")
                    
                    msg = (
                        f"🎬 <b>تم اصطياد وإضافة حلقة جديدة:</b> [{info['series_id']}]\n"
                        f"📺 <b>الحلقة {target_ep}</b>\n"
                        f"🌐 <b>الموقع:</b> <code>تمت الإضافة المبدئية بنجاح ✅</code>\n\n"
                        f"⏳ <i>جاري مراقبة الحلقة للبحث عن باقي المصادر...</i>"
                    )
                    bot.send_message(ADMIN_ID, msg, parse_mode="HTML")
            except Exception as e:
                bot.send_message(ADMIN_ID, f"⚠️ خطأ في API للمسلسل {info['series_id']}: {e}")

        is_laroza_needed = bool(info.get("laroza_url"))
        is_qdrama_needed = bool(info.get("qdrama_url"))
        
        if (not is_laroza_needed or info["laroza_done"]) and (not is_qdrama_needed or info["qdrama_done"]):
            if info["status"] == "partial":
                info["current_episode"] = target_ep
                info["status"] = "waiting"
                info["watch_urls"] = []
                info["down_urls"] = []
                info["laroza_done"] = False
                info["qdrama_done"] = False
                
                if info.get("laroza_new_url"): info["laroza_url"] = info["laroza_new_url"]
                if info.get("qdrama_new_url"): info["qdrama_url"] = info["qdrama_new_url"]
                
                msg = f"🔒 <b>تم قفل الحلقة:</b> [{info['series_id']}] (الحلقة {target_ep})\nالسبب: ✅ اكتملت جميع الجودات والمصادر\n<i>البوت هيبدأ يبحث عن الحلقة القادمة...</i>"
                bot.send_message(ADMIN_ID, msg, parse_mode="HTML")
                changed = True
                
    if changed: save_data(data)

def format_duration(total_seconds):
    seconds = max(0, int(total_seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours}س {minutes}د {seconds}ث"

def build_status_message():
    uptime = (datetime.now(timezone.utc) - started_at).total_seconds()
    data = load_data()
    lines = [
        "✅ <b>البوت شغّال وبيفحص بانتظام!</b>\n",
        f"⏱ <b>وقت التشغيل:</b> {format_duration(uptime)}",
        f"🔍 <b>آخر فحص:</b> {last_scan_at.astimezone().strftime('%Y-%m-%d %H:%M:%S') if last_scan_at else 'لم يبدأ'}",
        f"🔄 <b>دورات الفحص:</b> {scan_cycles} | ➕ <b>الإشعارات:</b> {total_added}\n",
        f"📁 <b>مسار البيانات:</b> <code>{DATA_FILE}</code>\n",
        "📺 <b>آخر حالة:</b>",
    ]
    if not data:
        lines.append("  لا توجد عناصر مضافة.")
    else:
        for slug, info in data.items():
            ep = info.get('current_episode', 0)
            target_ep = int(ep) + 1 if str(ep).isdigit() else '؟'
            
            raw_status = info.get('status', 'waiting')
            if raw_status == 'waiting':
                state = "✅ مستعد"
            elif raw_status == 'partial':
                state = f"⏳ جاري تجميع باقي المصادر لحلقة {target_ep}..."
            else:
                state = raw_status
                
            lines.append(f"  🎬 <b>{slug}</b>: حلقة {ep} | {state}")
            
    return "\n".join(lines)

def run_scheduler():
    schedule.every(CHECK_INTERVAL_SECONDS).seconds.do(check_new_episodes)
    while True:
        schedule.run_pending()
        time.sleep(1)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if str(message.chat.id) != str(ADMIN_ID): return
    text = """🤖 <b>نظام المراقبة الذكي (دعم تجميع المصادر)</b>

🔹 <code>/add</code> — إضافة جديد
🔹 <code>/del</code> — حذف
🔹 <code>/list</code> أو <code>/check</code> أو <code>/status</code> — الحالة
🔹 <code>/backup</code> — نسخة احتياطية
🔹 <code>/restore</code> — استعادة البيانات"""
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['add'])
def add_series(message):
    if str(message.chat.id) != str(ADMIN_ID): return
    try:
        parts = message.text.split(" ")
        if len(parts) != 5:
            bot.reply_to(message, "الصيغة خاطئة!\nاستخدم:\n/add [رابط_لاروزا] [رابط_كيودراما] [رقم_الحلقة_الحالية] [ID_المسلسل]\n* ضع none مكان الرابط إذا لم يتوفر.")
            return
            
        laroza_url = parts[1] if parts[1].lower() != "none" else ""
        qdrama_url = parts[2] if parts[2].lower() != "none" else ""
        current_ep = int(parts[3])
        series_id = parts[4]

        if laroza_url and "video.php" not in laroza_url:
            bot.reply_to(message, "❌ رابط لاروزا يجب أن يكون رابط حلقة فعلية (يحتوي على video.php)")
            return
        if qdrama_url and "watch.php" not in qdrama_url:
            bot.reply_to(message, "❌ رابط كيو دراما يجب أن يكون رابط حلقة فعلية (يحتوي على watch.php)")
            return
            
        data = load_data()
        bot.reply_to(message, f"⏳ جاري حفظ سيرفرات حلقة {current_ep} للمسلسل [{series_id}] في الخلفية...")
        
        laroza_seen_fps = []
        if laroza_url:
            ep_data = get_laroza_ep(laroza_url, current_ep)
            if ep_data and ep_data.get("watch_urls"):
                for w in ep_data["watch_urls"]:
                    fp = extract_video_fingerprint(w)
                    if fp: laroza_seen_fps.append(fp)
                    
        data[series_id] = {
            "series_id": series_id,
            "laroza_url": laroza_url,
            "qdrama_url": qdrama_url,
            "current_episode": current_ep,
            "status": "waiting",
            "watch_urls": [],
            "down_urls": [],
            "laroza_done": False,
            "qdrama_done": False,
            "laroza_seen_fps": laroza_seen_fps
        }
        save_data(data)
        bot.reply_to(message, f"✅ تمت الإضافة بنجاح للمعرف [{series_id}]! البوت مستعد למراقبة الحلقة {current_ep + 1}.")
    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ: {str(e)}")

@bot.message_handler(commands=['del'])
def del_series(message):
    if str(message.chat.id) != str(ADMIN_ID): return
    try:
        parts = message.text.split(" ")
        if len(parts) != 2:
            bot.reply_to(message, "الصيغة خاطئة!\nاستخدم:\n/del [ID_المسلسل]")
            return
            
        series_id = parts[1]
        data = load_data()
        
        if series_id in data:
            del data[series_id]
            save_data(data)
            bot.reply_to(message, f"✅ تم الحذف.")
        else:
            bot.reply_to(message, "❌ هذا المسلسل غير موجود في قائمة المراقبة.")
    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ: {str(e)}")

@bot.message_handler(commands=['list', 'status', 'check'])
def list_series(message):
    if str(message.chat.id) != str(ADMIN_ID): return
    bot.reply_to(message, build_status_message(), parse_mode="HTML")

@bot.message_handler(commands=['backup'])
def backup_data(message):
    if str(message.chat.id) != str(ADMIN_ID): return
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "rb") as f:
            bot.send_document(message.chat.id, f, caption="✅ نسخة احتياطية (series.json)")
    else:
        bot.reply_to(message, "⚠️ لا توجد بيانات للنسخ.")

@bot.message_handler(commands=["restore"])
def restore_data_step(message):
    if str(message.chat.id) != str(ADMIN_ID): return
    msg = bot.reply_to(message, "📥 <b>أرسل لي ملف series.json كرسالة (Document) أو نص:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_restore)

def process_restore(message):
    if str(message.chat.id) != str(ADMIN_ID): return
    raw_data = ""
    try:
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            raw_data = downloaded_file.decode('utf-8')
        elif message.text:
            raw_data = message.text
        else:
            bot.reply_to(message, "❌ ملف أو نص غير صالح.")
            return
        parsed_data = json.loads(raw_data)
        save_data(parsed_data)
        bot.reply_to(message, "✅ <b>تمت الاستعادة بنجاح!</b>", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ خطأ: {e}")


if __name__ == "__main__":
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    print("Bot is running...")
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"[ERROR] Polling crashed: {e}. Restarting...", flush=True)
            time.sleep(5)
