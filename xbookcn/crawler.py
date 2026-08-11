import time
import random
import json
import os
import re
import logging
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urljoin, unquote
import pymongo
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv()

log_dir = 'logs'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_file = os.path.join(log_dir, f'crawler_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class XbookCnCrawler:
    BASE_URL = "https://book.xbookcn.net"

    CATEGORIES = {
        "通俗小说": "/p/popular.html",
        "都市小说": "/p/urban.html",
        "武侠小说": "/p/martial.html",
        "奇幻小说": "/p/fantasy.html",
        "冒险小说": "/p/adventure.html",
        "穿越小说": "/p/history.html",
        "黑暗小说": "/p/dark.html",
        "言情小说": "/p/romance.html",
    }

    def __init__(self):
        mongo_host = os.getenv('MONGO_HOST', 'localhost')
        mongo_port = int(os.getenv('MONGO_PORT', '27017'))
        mongo_user = os.getenv('MONGO_USER', '')
        mongo_pass = os.getenv('MONGO_PASS', '')
        mongo_db = os.getenv('MONGO_DB', 'xbookcn_db')

        if mongo_user and mongo_pass:
            self.client = pymongo.MongoClient(
                host=mongo_host,
                port=mongo_port,
                username=mongo_user,
                password=mongo_pass
            )
        else:
            self.client = pymongo.MongoClient(mongo_host, mongo_port)

        self.db = self.client[mongo_db]
        self.collection = self.db['xbookcn']

        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')

        self.driver = uc.Chrome(options=chrome_options, version_main=None)
        self.wait = WebDriverWait(self.driver, 20)

    def get_page(self, url, max_retries=5):
        for attempt in range(max_retries):
            try:
                self.driver.get(url)
                time.sleep(3)

                page_source = self.driver.page_source
                if 'Just a moment' in page_source or 'challenge' in page_source.lower():
                    logger.info(f"  Cloudflare challenge detected, waiting... (attempt {attempt+1})")
                    time.sleep(5)
                    page_source = self.driver.page_source

                if 'Just a moment' not in page_source:
                    return page_source

                logger.warning(f"  Still on challenge page (attempt {attempt+1})")
                time.sleep(3)
            except Exception as e:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"请求失败 (第{attempt+1}/{max_retries}次): {url} - {e}, {wait_time:.1f}秒后重试")
                time.sleep(wait_time)

        logger.error(f"请求彻底失败: {url}")
        return None

    def sleep_random(self, lo=1.0, hi=3.0):
        time.sleep(random.uniform(lo, hi))

    def load_existing_novels(self):
        existing = set()
        for doc in self.collection.find({}, {'novel_url': 1}):
            existing.add(doc['novel_url'])
        return existing

    def get_category_novels(self, category_name, category_path):
        url = self.BASE_URL + category_path
        logger.info(f"[Category] {category_name}: {url}")
        html = self.get_page(url)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        novels = []
        for post in soup.select('.post'):
            body = post.select_one('.post-body')
            if not body:
                continue
            for a in body.find_all('a', href=True):
                href = a['href']
                text = a.get_text(strip=True)
                if text and '/search/label/' in href:
                    if not href.startswith('http'):
                        href = urljoin(self.BASE_URL, href)
                    novels.append({'title': text, 'url': href})
        return novels

    def get_novel_chapters(self, novel_url):
        chapters = []
        url = novel_url
        page = 1

        while url:
            html = self.get_page(url)
            if not html:
                break

            soup = BeautifulSoup(html, 'html.parser')
            for post in soup.select('.post'):
                title_el = post.select_one('.post-title a')
                if not title_el:
                    continue
                href = title_el.get('href', '')
                title = title_el.get_text(strip=True)
                if href and title:
                    if not href.startswith('http'):
                        href = urljoin(self.BASE_URL, href)
                    chapters.append({'title': title, 'url': href})

            older = soup.select_one('.blog-pager-older-link')
            if older and older.get('href'):
                url = older['href']
                page += 1
                logger.info(f"    Next page {page}")
                self.sleep_random(1.0, 2.0)
            else:
                url = None

        return chapters

    def get_chapter_content(self, chapter_url):
        html = self.get_page(chapter_url)
        if not html:
            return ""

        soup = BeautifulSoup(html, 'html.parser')
        post_body = soup.select_one('.post-body')
        if not post_body:
            return ""

        for tag in post_body.find_all(['script', 'style']):
            tag.decompose()
        for div in post_body.find_all('div', class_='clear'):
            div.decompose()

        return post_body.get_text(strip=True)

    def crawl_novel(self, novel_title, novel_url, category="", existing_novels=None):
        if novel_url in existing_novels:
            logger.info(f"  [Skip] {novel_title} (already crawled)")
            return

        logger.info(f"  [Crawl] {novel_title}: {novel_url}")
        chapters = self.get_novel_chapters(novel_url)
        if not chapters:
            logger.warning(f"    No chapters found, skipping.")
            return

        logger.info(f"    Found {len(chapters)} chapters")
        chapter_data = []
        for i, ch in enumerate(chapters):
            logger.info(f"    Chapter {i+1}/{len(chapters)}: {ch['title']}")
            content = self.get_chapter_content(ch["url"])
            chapter_data.append({
                "title": ch["title"],
                "url": ch["url"],
                "content": content,
            })
            self.sleep_random(1.0, 2.5)

        novel_data = {
            "title": novel_title,
            "novel_url": novel_url,
            "category": category,
            "chapter_count": len(chapter_data),
            "crawled_at": datetime.now(),
            "chapters": chapter_data,
        }
        self.collection.insert_one(novel_data)
        existing_novels.add(novel_url)
        logger.info(f"    Saved: {novel_title} ({len(chapter_data)} chapters)")

    def run(self):
        logger.info("=" * 60)
        logger.info("XbookCn Novel Crawler")
        logger.info("=" * 60)

        try:
            existing_novels = self.load_existing_novels()
            logger.info(f"已存在 {len(existing_novels)} 部小说，跳过爬取")

            for cat_name, cat_path in self.CATEGORIES.items():
                logger.info(f"\n{'='*50}")
                logger.info(f"Category: {cat_name}")
                logger.info(f"{'='*50}")

                novels = self.get_category_novels(cat_name, cat_path)
                logger.info(f"  Found {len(novels)} novels in {cat_name}")

                for i, novel in enumerate(novels):
                    logger.info(f"\n  [{i+1}/{len(novels)}] {novel['title']}")
                    try:
                        self.crawl_novel(novel["title"], novel["url"], category=cat_name, existing_novels=existing_novels)
                    except Exception as e:
                        logger.error(f"    Error: {e}")
                    self.sleep_random(2.0, 4.0)

                self.sleep_random(3.0, 5.0)

        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            self.driver.quit()
            self.client.close()
            logger.info("\nDone.")


if __name__ == "__main__":
    crawler = XbookCnCrawler()
    crawler.run()
