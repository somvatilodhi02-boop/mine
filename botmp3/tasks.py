import os
import time
import math
import glob
import random
import logging
import subprocess
import re
import requests
import yt_dlp
from celery import Celery
import config

# ================= SETUP =================
app = Celery('tasks', broker=config.REDIS_URL)

app.conf.update(
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    broker_pool_limit=None,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

COOKIE_POOL_DIR = "cookie_pool"

# ================= HELPERS =================

def get_random_cookie():
    if not os.path.exists(COOKIE_POOL_DIR): return None
    files = [f for f in os.listdir(COOKIE_POOL_DIR) if f.endswith('.txt')]
    if not files: return None
    return os.path.join(COOKIE_POOL_DIR, random.choice(files))

def format_size(bytes_size):
    if not bytes_size: return "0 B"
    size_name = ("B", "KB", "MB", "GB")
    i = int(math.floor(math.log(bytes_size, 1024)))
    p = math.pow(1024, i)
    s = round(bytes_size / p, 2)
    return "%s %s" % (s, size_name[i])

def sanitize_filename(title):
    clean = re.sub(r'[\\/*?:"<>|]', '', title)
    clean = clean.encode('ascii', 'ignore').decode('ascii')
    clean = clean.strip()[:60]
    if not clean: clean = "audio"
    return f"{clean}.mp3"

def telegram_api(method, data=None, files=None):
    url = f"{config.LOCAL_API_URL}{config.BOT_TOKEN}/{method}"
    try:
        response = requests.post(url, data=data, files=files, timeout=1200)
        return response.json()
    except Exception as e:
        logger.error(f"API Request Failed: {e}")
        return None

def edit_message(chat_id, message_id, text):
    telegram_api("editMessageText", data={
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    })

def fix_thumbnail(path):
    if not path: return None
    new_path = os.path.splitext(path)[0] + "_cover.jpg"
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-i', path, '-vf', 'scale=320:320:force_original_aspect_ratio=decrease', '-q:v', '2', new_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return new_path if os.path.exists(new_path) else path
    except:
        return path

# ================= MAIN TASK =================

@app.task(bind=True, acks_late=True)
def process_audio(self, url, chat_id, message_id, user_name):
    task_id = self.request.id
    out_base = f"{config.DOWNLOAD_DIR}/{task_id}"
    
    if not os.path.exists(config.DOWNLOAD_DIR):
        os.makedirs(config.DOWNLOAD_DIR)

    progress_state = {'last_update': 0}

    def progress_hook(d):
        if d['status'] == 'downloading':
            current_time = time.time()
            if current_time - progress_state['last_update'] > 4:
                progress_state['last_update'] = current_time
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                speed = d.get('speed', 0)
                percent = (downloaded / total * 100) if total else 0
                msg = f"⬇️ ** Downloading... **\n`{percent:.1f}%` | `{format_size(speed)}/s`"
                edit_message(chat_id, message_id, msg)
        elif d['status'] == 'finished':
            edit_message(chat_id, message_id, "⚙️ ** Processing Audio... **")

    cookie_file = get_random_cookie()
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f"{out_base}.%(ext)s",
        'writethumbnail': True,
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'cookiefile': cookie_file,
        
        # --- FIX: STRICTLY FORCE ANDROID CLIENT ---
        # 1. We disable the cache to ensure old "blocked" sessions don't linger.
        'cachedir': False,
        
        # 2. We strictly use ONLY the 'android' client. 
        #    We DO NOT include 'web' or 'tv' to ensure it never hits the HTML login page.
        'extractor_args': {
            'youtube': {
                'player_client': ['android'],
                'player_skip': ['webpage', 'configs'],
                'include_sponserblock': False
            }
        },
        # --- END FIX ---
        
        'postprocessors': [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
            {'key': 'FFmpegMetadata'},
            {'key': 'EmbedThumbnail'},
        ],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Audio')
            duration = info.get('duration')
            performer = info.get('uploader')

        edit_message(chat_id, message_id, "⬆️ ** Uploading... **")
        mp3_file = f"{out_base}.mp3"
        
        if not os.path.exists(mp3_file):
            raise Exception("Conversion failed")

        raw_thumb = None
        final_thumb = None
        for f in glob.glob(f"{out_base}.*"):
            if f.endswith(('.jpg', '.png', '.webp')) and not f.endswith('.mp3'):
                raw_thumb = f
                break
        
        if raw_thumb:
            final_thumb = fix_thumbnail(raw_thumb)

        official_filename = sanitize_filename(title)

        with open(mp3_file, 'rb') as audio:
            files = {
                'audio': (official_filename, audio, 'audio/mpeg')
            }
            
            thumb_obj = None
            if final_thumb and os.path.exists(final_thumb):
                thumb_obj = open(final_thumb, 'rb')
                files['thumbnail'] = ('cover.jpg', thumb_obj, 'image/jpeg')
            
            data = {
                'chat_id': chat_id,
                'title': title,
                'performer': performer,
                'duration': duration,
                'caption': f"💿 {title}"
            }
            
            telegram_api("sendAudio", data=data, files=files)
            
            if thumb_obj: thumb_obj.close()

        telegram_api("deleteMessage", data={'chat_id': chat_id, 'message_id': message_id})
        
    except Exception as e:
        logger.error(f"Task Failed: {e}")
        edit_message(chat_id, message_id, f"❌ Error: {str(e)}")
    
    finally:
        for f in glob.glob(f"{out_base}*"):
            try: os.remove(f)
            except: pass
