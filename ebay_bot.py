import os
import re
import json
import time
import random
import logging
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# ================= CONFIGURATION =================

TARGET_URLS = [
    "https://www.ebay.com/sch/i.html?_nkw=vintage+t+shirt+80s&_sacat=0&_from=R40&_fsrp=1&_dcat=15687&LH_PrefLoc=3&_oac=1&LH_BIN=1&_sop=10",
    "https://www.ebay.com/sch/i.html?_nkw=vintage+t+shirt+90s&_sacat=0&_from=R40&_fsrp=1&_dcat=15687&LH_PrefLoc=3&_oac=1&LH_BIN=1&_sop=10",
]

# Telegram Configuration
TELEGRAM_BOT_TOKEN = "8309474307:AAGe3qgQb-kWaO4jWC-FHlusT-M12DEA-rc"
TELEGRAM_CHAT_ID = "8279951666"

# Speed Settings
MIN_WAIT = 1
MAX_WAIT = 1
MAX_ITEMS_PER_PAGE = 3
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "seen_items.json")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger()

# ================= TIME FUNCTIONS =================

def get_current_times():
    """Calculates Thai and eBay (PST) time."""
    utc_now = datetime.now(timezone.utc)
    thai_time = utc_now + timedelta(hours=7)
    return {
        "thai": thai_time.strftime('%H:%M:%S'),
        "timestamp": thai_time.isoformat()
    }

# ================= NOTIFICATION FUNCTION =================

def send_telegram(title, link):
    """Sends a message to Telegram."""
    text = f"🔥 *New Listing Found!*\n\n{title}\n\n[View on eBay]({link})"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"✅ Sent Telegram: {title[:20]}...")
        else:
            logger.error(f"❌ Telegram Failed: {response.text}")
    except Exception as e:
        logger.error(f"Telegram Error: {e}")

# ================= UTILITY FUNCTIONS =================

def load_database():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except: pass
    return set()

def save_database(seen_set):
    try:
        data = list(seen_set)[-1000:] # Keep last 1000 items
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Save DB Failed: {e}")

def extract_id(url):
    m = re.search(r"/itm/(?:.*?/)?(\d+)", url)
    if not m: m = re.search(r"itm/(\d+)", url)
    return m.group(1) if m else None

def get_clean_title(soup_item, link_tag):
    raw_title = "New Listing"
    for sel in [".s-item__title", ".s-card__title", "h3"]:
        tag = soup_item.select_one(sel)
        if tag and len(tag.get_text(strip=True)) > 5:
            raw_title = tag.get_text(strip=True)
            break
            
    if raw_title == "New Listing" and link_tag:
        raw_title = link_tag.get_text(strip=True)

    garbage_phrases = ["Opens in a new window or tab", "New Listing", "Shop on eBay", "Results matching"]
    clean_title = raw_title
    for phrase in garbage_phrases:
        clean_title = clean_title.replace(phrase, "")
    return clean_title.strip()

def setup_browser():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-notifications")
    options.add_argument("--start-maximized")
    options.add_argument("--log-level=3")
    
    options.page_load_strategy = 'normal'
    
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

# ================= LOGIC =================

def parse_items_from_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    items_data = []
    all_links = soup.find_all("a", href=re.compile(r"/itm/"))
    seen_in_page = set()
    current_times = get_current_times()

    for link_tag in all_links:
        href = link_tag.get("href", "").split("?")[0]
        if href in seen_in_page: continue
        seen_in_page.add(href)
        
        item_id = extract_id(href)
        if not item_id: continue

        container = link_tag.find_parent(class_=re.compile(r"(s-item|s-card|vim)"))
        if not container: container = link_tag.parent

        full_text_check = container.get_text(" ", strip=True)
        if "Shop on eBay" in full_text_check or "Results matching" in full_text_check:
            continue 

        title = get_clean_title(container, link_tag)

        items_data.append({
            "id": item_id,
            "title": title,
            "link": href,
            "times": current_times
        })
        
        if len(items_data) >= MAX_ITEMS_PER_PAGE: break
    
    return items_data

def main():
    print("="*50)
    print("{eBay Finder - Telegram Version}")
    print("="*50)

    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f: f.write("[]")
    
    seen = load_database()
    logger.info(f"Loaded {len(seen)} items from history.")

    driver = setup_browser()

    try:
        logger.info("Opening Login Page...")
        driver.get("https://www.ebay.com/signin/")
        input("\n⚠️  PLEASE LOGIN (Solve Captcha if needed), then press [ENTER] here...\n")
        logger.info("Starting monitoring loop...")
        
        first_run = True
        
        while True:
            for url in TARGET_URLS:
                try:
                    driver.get(url)
                    items = parse_items_from_html(driver.page_source)
                    
                    for item in items:
                        if item['id'] in seen: continue
                        
                        seen.add(item['id'])
                        save_database(seen)
                        
                        if not first_run:
                            # ส่งทันทีที่เจอ ไม่ต้องรอ loop URL อื่น
                            logger.info(f"{item['times']['thai']} | {item['title'][:20]}...")
                            send_telegram(item['title'], item['link'])
                        else:
                            logger.info(f"Memorized: {item['title'][:20]}... [{item['id']}]")

                except Exception as e:
                    logger.error(f"Loop Error: {e}")
                    time.sleep(1)
            
            # หลังจากรันครบทุก URL ในรอบแรกแล้ว ให้ปลดล็อกการส่งข้อความ
            if first_run:
                logger.info("Initial scan completed. Messaging enabled.")
                first_run = False
            
            delay = random.uniform(MIN_WAIT, MAX_WAIT)
            logger.info(f"💤 Sleeping {delay:.1f}s...")
            time.sleep(delay)

    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Critical Crash: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
