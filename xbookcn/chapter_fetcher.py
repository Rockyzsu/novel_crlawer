import time
import random
import os
import logging
import redis
import requests
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urljoin
import pymongo
from bs4 import BeautifulSoup

load_dotenv()

log_dir = 'logs'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_file = os.path.join(log_dir, f'worker_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ChapterFetcher:
    def __init__(self, worker_id):
        self.worker_id = worker_id

        mongo_host = os.getenv('MONGO_HOST', 'localhost')
        mongo_port = int(os.getenv('MONGO_PORT', '27017'))
        mongo_user = os.getenv('MONGO_USER', '')
        mongo_pass = os.getenv('MONGO_PASS', '')
        mongo_db = os.getenv('MONGO_DB', 'xbookcn_db')

        if mongo_user and mongo_pass:
            self.mongo_client = pymongo.MongoClient(
                host=mongo_host, port=mongo_port,
                username=mongo_user, password=mongo_pass
            )
        else:
            self.mongo_client = pymongo.MongoClient(mongo_host, mongo_port)

        self.db = self.mongo_client[mongo_db]
        self.collection = self.db['xbookcn']

        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_port = int(os.getenv('REDIS_PORT', '6379'))
        redis_db = int(os.getenv('REDIS_DB', '0'))
        redis_pass = os.getenv('REDIS_PASS', '')

        self.redis = redis.Redis(
            host=redis_host, port=redis_port, db=redis_db,
            password=redis_pass, decode_responses=True
        )
        self.lock_prefix = 'xbookcn:lock:'

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        })

    def fetch_page(self, url, max_retries=3):
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    return resp.text
                logger.warning(f"  HTTP {resp.status_code} for {url}")
            except Exception as e:
                logger.warning(f"  Fetch error (attempt {attempt+1}): {e}")

            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) + random.uniform(0, 1))

        return None

    def parse_chapter_content(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        post_body = soup.select_one('.post-body')
        if not post_body:
            return ""

        for tag in post_body.find_all(['script', 'style']):
            tag.decompose()
        for div in post_body.find_all('div', class_='clear'):
            div.decompose()

        return post_body.get_text(strip=True)

    def claim_novel(self, novel_url):
        lock_key = self.lock_prefix + novel_url
        return self.redis.set(lock_key, self.worker_id, nx=True, ex=3600)

    def release_novel(self, novel_url):
        lock_key = self.lock_prefix + novel_url
        self.redis.delete(lock_key)

    def process_novel(self, novel):
        novel_url = novel['novel_url']
        novel_id = novel['_id']

        if not self.claim_novel(novel_url):
            return False

        logger.info(f"[Worker-{self.worker_id}] Claimed: {novel['title']}")

        try:
            chapters = novel.get('chapters', [])
            total = len(chapters)

            for i, chapter in enumerate(chapters):
                if chapter.get('content'):
                    continue

                chapter_url = chapter['url']
                logger.info(f"  [{i+1}/{total}] {chapter['title']}")

                html = self.fetch_page(chapter_url)
                if not html:
                    logger.warning(f"    Failed to fetch: {chapter_url}")
                    continue

                content = self.parse_chapter_content(html)

                self.collection.update_one(
                    {'_id': novel_id, 'chapters.url': chapter_url},
                    {'$set': {'chapters.$.content': content}}
                )

                time.sleep(random.uniform(1.0, 2.5))

            self.collection.update_one(
                {'_id': novel_id},
                {'$set': {'content_fetched': True, 'fetched_at': datetime.now()}}
            )

            logger.info(f"[Worker-{self.worker_id}] Done: {novel['title']} ({total} chapters)")
            return True

        except Exception as e:
            logger.error(f"[Worker-{self.worker_id}] Error: {novel['title']} - {e}")
            return False

        finally:
            self.release_novel(novel_url)

    def run(self):
        logger.info(f"[Worker-{self.worker_id}] Started")

        while True:
            try:
                novel = self.collection.find_one_and_update(
                    {'content_fetched': False},
                    {'$set': {'claimed_by': self.worker_id, 'claimed_at': datetime.now()}},
                    return_document=pymongo.ReturnDocument.AFTER
                )

                if not novel:
                    logger.info(f"[Worker-{self.worker_id}] No pending novels, waiting...")
                    time.sleep(10)
                    continue

                self.process_novel(novel)

                time.sleep(random.uniform(2.0, 4.0))

            except KeyboardInterrupt:
                logger.info(f"[Worker-{self.worker_id}] Stopped")
                break
            except Exception as e:
                logger.error(f"[Worker-{self.worker_id}] Error: {e}")
                time.sleep(5)

        self.mongo_client.close()
        self.redis.close()


if __name__ == '__main__':
    import sys
    worker_id = sys.argv[1] if len(sys.argv) > 1 else '1'
    fetcher = ChapterFetcher(worker_id)
    fetcher.run()
