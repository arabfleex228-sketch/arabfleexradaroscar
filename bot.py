import telebot
import requests
import time
import json
import os
import re
import threading
import schedule
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

TOKEN = "7808630939:AAEY0_q6vnkKlMRjvXNmEXwK1G80hv0vghY" # ضع توكن البوت هنا
ADMIN_ID = 1013251619 # ضع الأي دي الخاص بك هنا
API_URL = "https://arabfleex.xo.je/api.php" # رابط الـ API الخاص بموقعك
SECRET_KEY = "ArabFleex_2024_SecRet"

CHECK_INTERVAL_SECONDS = 150 # الفحص كل دقيقتين ونصف
DATA_FILE = "series.json"

bot = telebot.TeleBot(TOKEN)

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
    """التحقق من خلو الرابط من السيرفرات المحظورة والإعلانات"""
    if not url: return False
    url_lower = url.lower()
    for banned in BANNED_SERVERS:
        if banned in url_lower:
            return False
    return True

def extract_video_fingerprint(url):
    """استخراج بصمة فريدة لرابط الفيديو لمنع التكرار (مخصص للاروزا)"""
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
    """فلترة وترتيب السيرفرات بناءً على المصدر (لاروزا أو كيو دراما)"""
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
        # ترتيب المشاهدة المخصص للاروزا (vidspeed في الصدارة)
        w1 = pop_match(w_pool, ['vidspeed'])
        if not w1: w1 = pop_match(w_pool, ['uqload'])
        
        w2 = pop_match(w_pool, ['uqload']) 
        if not w2: w2 = pop_match(w_pool, ['liiivideo', 'livideo'])
    else:
        # ترتيب المشاهدة المخصص لكيو دراما (liiivideo في الصدارة)
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

    # التحميل دايماً liiivideo ثم uqload كأولوية للموقعين
    d1 = pop_match(d_pool, ['liiivideo', 'livideo'])
    if not d1: d1 = pop_match(d_pool, ['uqload'])
    
    d2 = pop_match(d_pool, ['uqload'])
    if not d2: d2 = pop_match(d_pool, ['1cloud', 'voe'])
    
    if not d1 and d_pool: d1 = d_pool.pop(0)
    if not d2 and d_pool: d2 = d_pool.pop(0)

    return w1, w2, w3, w4, d1, d2

def get_working_laroza_url(url):
    """تحديث دومين لاروزا تلقائياً إذا تم حظره"""
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
        
        # العثور على الموسم النشط فقط وتجاهل المواسم القديمة المخفية
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
        
        # استخراج سيرفرات المشاهدة من الـ iframe أو data-embed-url
        for li in soup_ep.find_all('li', attrs={"data-embed-url": True}):
            src = li.get('data-embed-url', '')
            if src.startswith("//"): src = "https:" + src
            if is_valid_url(src): watch_urls.append(src)
            
        for iframe in soup_ep.find_all('iframe'):
            src = iframe.get('src', '')
            if src.startswith("//"): src = "https:" + src
            if is_valid_url(src) and src not in watch_urls:
                watch_urls.append(src)

        # استخراج سيرفرات التحميل
        download_list = soup_ep.find('ul', class_='downloadlist')
        if download_list:
            for a in download_list.find_all('a'):
                href = a.get('href')
                if href and is_valid_url(href):
                    down_urls.append(href)
                    
        # الحماية المزدوجة (التحميل + البصمة)
        if not down_urls:
            return None # حلقة وهمية - لا يوجد تحميل
            
        # فحص البصمات (Fingerprint)
        for w in watch_urls:
            fp = extract_video_fingerprint(w)
            if fp:
                if fp in seen_fps:
                    return None # بصمة مكررة = حلقة وهمية
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
                    
        # الحماية: لو مفيش روابط تحميل = حلقة وهمية/إعلان
        if not down_urls:
            return None
            
        return {"watch_urls": watch_urls, "down_urls": down_urls, "url": ep_link}
    except Exception as e:
        print(f"Qdrama Error: {e}")
        return None

def check_new_episodes():
    data = load_data()
    changed = False
    
    for key, info in data.items():
        if info["status"] != "waiting": continue
        
        target_ep = info["current_episode"] + 1
        new_discovery = False
        
        # تهيئة مصفوفة البصمات إذا لم تكن موجودة
        if "laroza_seen_fps" not in info:
            info["laroza_seen_fps"] = []

        if info.get("laroza_url") and not info["laroza_done"]:
            res = get_laroza_ep(info["laroza_url"], target_ep, info["laroza_seen_fps"])
            if res:
                info["watch_urls"].extend(res["watch_urls"])
                info["down_urls"].extend(res["down_urls"])
                info["laroza_seen_fps"].extend(res["new_fps"]) # تحديث البصمات
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
            
            # تحديد المصدر لمعرفة الترتيب المطلوب
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
                    source_str = []
                    if info["laroza_done"]: source_str.append("لاروزا")
                    if info["qdrama_done"]: source_str.append("كيو دراما")
                    
                    bot.send_message(
                        ADMIN_ID, 
                        f"🎬 تم اصطياد وتحديث الحلقة {target_ep}\n"
                        f"📺 المسلسل ID: {info['series_id']}\n"
                        f"🪝 المصادر: {' + '.join(source_str)} ✅\n"
                        f"🌐 حالة الـ API: {r.text.strip()}\n\n"
                        f"⏳ في انتظار المصدر الآخر لاكتمال التجميع..."
                    )
            except Exception as e:
                bot.send_message(ADMIN_ID, f"⚠️ خطأ في API للمسلسل {info['series_id']}: {e}")

        # التحقق من اكتمال المصادر أو عدم وجود مصدر آخر
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
                
                # تحديث روابط البداية للمواسم عشان يكمل منها
                if info.get("laroza_new_url"): info["laroza_url"] = info["laroza_new_url"]
                if info.get("qdrama_new_url"): info["qdrama_url"] = info["qdrama_new_url"]
                
                bot.send_message(ADMIN_ID, f"🔒 تم قفل الحلقة {target_ep} (ID: {info['series_id']})\nالسبب: اكتملت جميع المصادر المتاحة وبدأ مراقبة الحلقة القادمة.")
                changed = True
                
    if changed: save_data(data)

def run_scheduler():
    schedule.every(CHECK_INTERVAL_SECONDS).seconds.do(check_new_episodes)
    while True:
        schedule.run_pending()
        time.sleep(1)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if message.chat.id != ADMIN_ID: return
    text = """
مرحباً بك في بوت جلب المسلسلات المتطور 🚀
الأوامر المتاحة:
/add - لإضافة مسلسل جديد للمراقبة
/del - لحذف مسلسل من المراقبة
/list أو /status - لعرض المسلسلات المراقبة
/backup - لأخذ نسخة احتياطية من البيانات
/restore - لاستعادة البيانات من ملف
"""
    bot.reply_to(message, text)

@bot.message_handler(commands=['add'])
def add_series(message):
    if message.chat.id != ADMIN_ID: return
    try:
        parts = message.text.split(" ")
        if len(parts) != 5:
            bot.reply_to(message, "الصيغة خاطئة!\nاستخدم:\n/add [رابط_لاروزا] [رابط_كيودراما] [رقم_الحلقة_الحالية] [ID_المسلسل]\n* ضع none مكان الرابط إذا لم يتوفر.")
            return
            
        laroza_url = parts[1] if parts[1].lower() != "none" else ""
        qdrama_url = parts[2] if parts[2].lower() != "none" else ""
        current_ep = int(parts[3])
        series_id = parts[4]

        # فلترة مبدئية لروابط الإضافة لضمان أنها صحيحة
        if laroza_url and "video.php" not in laroza_url:
            bot.reply_to(message, "❌ رابط لاروزا يجب أن يكون رابط حلقة فعلية (يحتوي على video.php)")
            return
        if qdrama_url and "watch.php" not in qdrama_url:
            bot.reply_to(message, "❌ رابط كيو دراما يجب أن يكون رابط حلقة فعلية (يحتوي على watch.php)")
            return
            
        data = load_data()
        
        bot.reply_to(message, f"⏳ جاري حفظ سيرفرات حلقة {current_ep} للمسلسل [ID: {series_id}] في الخلفية (لمنع الحلقات الوهمية)...")
        
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
        
        bot.reply_to(message, f"✅ تم الانتهاء من تخزين سيرفرات [ID: {series_id}] بنجاح! البوت مستعد لمراقبة حلقة {current_ep + 1}.")
    except Exception as e:
        bot.reply_to(message, f"حدث خطأ: {str(e)}")

@bot.message_handler(commands=['del'])
def del_series(message):
    if message.chat.id != ADMIN_ID: return
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
            bot.reply_to(message, f"✅ تم حذف المسلسل ذو الـ ID {series_id} من المراقبة بنجاح.")
        else:
            bot.reply_to(message, "❌ هذا الـ ID غير موجود في قائمة المراقبة.")
    except Exception as e:
        bot.reply_to(message, f"حدث خطأ: {str(e)}")

@bot.message_handler(commands=['list', 'status'])
def list_series(message):
    if message.chat.id != ADMIN_ID: return
    data = load_data()
    if not data:
        bot.reply_to(message, "لا توجد مسلسلات تحت المراقبة حالياً.")
        return
        
    text = "📺 المسلسلات تحت المراقبة:\n\n"
    for k, v in data.items():
        sources = []
        if v.get('laroza_url'): sources.append("لاروزا")
        if v.get('qdrama_url'): sources.append("كيو دراما")
        sources_str = " + ".join(sources) if sources else "لا يوجد"
        
        text += f"▪️ ID: {k} | الحلقة الحالية: {v['current_episode']}\n"
        text += f"   المصادر: [{sources_str}]\n"
        text += f"   الحالة: {v['status']}\n\n"
    bot.reply_to(message, text)

@bot.message_handler(commands=['backup'])
def backup_data(message):
    if message.chat.id != ADMIN_ID: return
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "rb") as f:
            bot.send_document(message.chat.id, f, caption="✅ نسخة احتياطية (series.json)")
    else:
        bot.reply_to(message, "❌ لا توجد بيانات محفوظة بعد.")

@bot.message_handler(commands=['restore'])
def restore_data(message):
    if message.chat.id != ADMIN_ID: return
    bot.reply_to(message, "الرجاء إرسال ملف `series.json` الآن للقيام بالاستعادة، ثم قم بالرد عليه بكلمة `تأكيد` (غير مدعوم حالياً بشكل تلقائي بالكامل لتجنب الأخطاء، تواصل مع المطور).")

if __name__ == "__main__":
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    print("Bot is running...")
    bot.infinity_polling()
