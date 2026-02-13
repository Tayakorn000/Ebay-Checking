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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

# ================= CONFIGURATION =================

TARGET_URLS = [
    "https://www.ebay.com/sch/i.html?_nkw=vintage+t+shirt&LH_BIN=1&_sop=10",
    "https://www.ebay.com/sch/i.html?_nkw=vintage+polo+rainbow&LH_BIN=1&_sop=10",
    "https://www.ebay.com/sch/i.html?_nkw=converse+usa&LH_BIN=1&_sop=10",
    "https://www.ebay.com/sch/i.html?_nkw=3d+emblem+t+shirt&LH_BIN=1&_sop=10",
    "https://www.ebay.com/sch/i.html?_nkw=vintage+harley+t+shirt&LH_BIN=1&_sop=10"
]

DISCORD_BOT_TOKEN = "MTQ3MTU0MjcxMDk5MjM3MTc1Mg.GfBrqI.ptwt60SYZkEZAQCghp509Una17FW_2sFMrPJ3U"
DISCORD_CHANNEL_ID = "1471542454271348939"

# ปรับเวลาให้ช้าลง ตามที่ขอ (หน่วย: วินาที)
MIN_WAIT = 20
MAX_WAIT = 40
MAX_ITEMS_PER_PAGE = 5
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
    """คำนวณเวลาปัจจุบัน ทั้งไทยและ eBay (PST)"""
    utc_now = datetime.now(timezone.utc)
    
    # Thailand is UTC+7
    thai_time = utc_now + timedelta(hours=7)
    
    # eBay US (Pacific Time) is UTC-8 (Standard) or UTC-7 (DST)
    ebay_time = utc_now - timedelta(hours=8)
    
    return {
        "thai": thai_time.strftime('%H:%M:%S'),
        "ebay": ebay_time.strftime('%H:%M:%S (PST)'),
        "full_thai": thai_time.strftime('%d/%m/%Y %H:%M:%S'),
        "timestamp": thai_time.isoformat()
    }

def get_listing_date_text(soup_item):
    date_tag = soup_item.select_one(".s-item__listingDate, .s-item__dynamic .BOLD")
    if date_tag:
        return date_tag.get_text(strip=True)
    return ""

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
        data = list(seen_set)[-1000:]
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Save DB Failed: {e}")

def extract_id(url):
    m = re.search(r"/itm/(?:.*?/)?(\d+)", url)
    if not m: m = re.search(r"itm/(\d+)", url)
    return m.group(1) if m else None

def get_price_smart(soup_item):
    selectors = [
        ".s-item__price", 
        ".s-card__price",
        ".x-price-primary",
        ".x-price-approx__price",
        ".POSITIVE",
        "span.bold"
    ]
    
    for sel in selectors:
        tag = soup_item.select_one(sel)
        if tag:
            text = tag.get_text(strip=True)
            if any(c.isdigit() for c in text): return text

    full_text = soup_item.get_text(" ", strip=True)
    match = re.search(r"(THB|\$|USD)\s?[\d,.]+(\.\d{2})?", full_text)
    if match: return match.group(0)

    return None

def get_clean_title(soup_item, link_tag):
    raw_title = "New Listing"
    
    # 1. พยายามดึง Title ดิบๆ มาก่อน
    for sel in [".s-item__title", ".s-card__title", "h3"]:
        tag = soup_item.select_one(sel)
        if tag and len(tag.get_text(strip=True)) > 5:
            raw_title = tag.get_text(strip=True)
            break
            
    # ถ้ายังไม่เจอ ลองดึงจาก Alt Image
    if raw_title == "New Listing":
        img = soup_item.select_one("img")
        if img and img.get("alt") and "Shop on eBay" not in img.get("alt"):
            raw_title = img.get("alt")
    
    # ถ้ายังไม่เจออีก ดึงจาก Link Text
    if raw_title == "New Listing" and link_tag:
        raw_title = link_tag.get_text(strip=True)

    # 2. 🔥 CLEANING PROCESS (ลบคำขยะ)
    garbage_phrases = [
        "Opens in a new window or tab",
        "New Listing",
        "Shop on eBay",
        "Results matching"
    ]
    
    clean_title = raw_title
    for phrase in garbage_phrases:
        clean_title = clean_title.replace(phrase, "")
        
    return clean_title.strip()

def send_discord(title, price, link, image, listing_date, times):
    if not image: image = "https://upload.wikimedia.org/wikipedia/commons/1/1b/EBay_logo.svg"
    
    # Time Description
    time_info = f"🇹🇭 **TH:** {times['thai']}\n🇺🇸 **US:** {times['ebay']}"
    if listing_date:
        time_info += f"\n📅 **Listed:** {listing_date}"

    payload = {
        "embeds": [{
            "title": f"🔥 NEW: {title[:200]}", 
            "url": link,
            "description": f"Click: [Open Listing]({link})",
            "color": 0x00ff00,
            "fields": [
                {"name": "Price", "value": f"**{price}**", "inline": True},
                {"name": "Time Found", "value": time_info, "inline": True}
            ],
            "thumbnail": {"url": image},
            "footer": {"text": "eBay Finder Bot"},
        }]
    }
    try:
        requests.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
            json=payload,
            timeout=5
        )
        logger.info(f"✅ Sent: {price} | Time: {times['thai']}")
    except Exception as e:
        logger.error(f"Discord Error: {e}")

def setup_browser():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-notifications")
    options.add_argument("--start-maximized")
    options.add_argument("--log-level=3")
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
        
        if container and ("media-wrapper" in str(container.get("class", [])) or "image" in str(container.get("class", []))):
             parent_container = container.find_parent(class_=re.compile(r"(s-item|s-card)"))
             if parent_container:
                 container = parent_container

        if not container: container = link_tag.parent

        # ANTI-AD FILTER
        full_text_check = container.get_text(" ", strip=True)
        img_check = container.select_one("img")
        
        is_ad = False
        if "Shop on eBay" in full_text_check or "Results matching" in full_text_check: is_ad = True
        if img_check and "Shop on eBay" in img_check.get("alt", ""): is_ad = True
        
        if is_ad: continue 

        # EXTRACTION
        title = get_clean_title(container, link_tag)
        price = get_price_smart(container)
        
        # TIME EXTRACTION
        listing_date = get_listing_date_text(container)

        if not price or price == "Check Link":
            match = re.search(r"(THB|\$|USD)\s?[\d,.]+(\.\d{2})?", full_text_check)
            if match: 
                price = match.group(0)
            else:
                continue

        image = ""
        img_tag = container.select_one("img")
        if img_tag:
            if img_tag.get("data-src"): image = img_tag.get("data-src")
            elif img_tag.get("data-img-src"): image = img_tag.get("data-img-src")
            elif img_tag.get("src"): image = img_tag.get("src")
            if "spacer" in image or "s_1x2" in image: image = ""

        items_data.append({
            "id": item_id,
            "title": title,
            "price": price,
            "link": href,
            "image": image,
            "listing_date": listing_date,
            "times": current_times
        })
        
        if len(items_data) >= MAX_ITEMS_PER_PAGE: break
    
    return items_data

def main():
    print("="*50)
    print("{eBay Finder")
    print("="*50)

    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f: f.write("[]")
    
    seen = load_database()
    logger.info(f"Loaded {len(seen)} items from history.")

    driver = setup_browser()

    try:
        logger.info("Opening Login Page...")
        driver.get("https://www.ebay.com/signin/")
        input("\n⚠️  PLEASE LOGIN manually, then press [ENTER] here...\n")
        logger.info("Starting monitoring loop...")
        
        first_run = True
        
        while True:
            for url in TARGET_URLS:
                try:
                    driver.get(url)
                    try:
                        WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.TAG_NAME, "body"))
                        )
                        # 🔥 เพิ่มจุดนี้ครับ: รอ 5 วินาทีให้รูปโหลด (สำคัญมาก!)
                        time.sleep(5) 
                    except: pass

                    # Parsing
                    items = parse_items_from_html(driver.page_source)
                    
                    for item in items:
                        if item['id'] in seen: continue
                        
                        seen.add(item['id'])
                        save_database(seen)
                        
                        if not first_run:
                            # Print มีเวลาด้วย
                            logger.info(f"{item['times']['thai']} | {item['price']} | {item['title'][:20]}...")
                            send_discord(
                                item['title'], 
                                item['price'], 
                                item['link'], 
                                item['image'],
                                item['listing_date'],
                                item['times']
                            )
                        else:
                            logger.info(f"Memorized: {item['title'][:20]}... [{item['id']}]")

                except Exception as e:
                    logger.error(f"Loop Error: {e}")
                    time.sleep(3)

            if first_run:
                logger.info("Initial scan completed.")
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