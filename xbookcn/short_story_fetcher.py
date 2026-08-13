import time
import random
import os
import re
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv
import pymongo
from pymongo.errors import DocumentTooLarge
from bs4 import BeautifulSoup

load_dotenv()

log_dir = 'logs'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_file = os.path.join(log_dir, f'short_story_fetcher_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ShortStoryFetcher:
    """用 requests 爬取短篇小说内容"""
    
    def __init__(self):
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
        self.collection = self.db['xbookcn_short_stories']
        
        # 本地保存目录（用于保存过大的文档）
        self.local_save_dir = os.getenv('LOCAL_SAVE_DIR', '/root/videos/novel/xbookcn_short')
        if not os.path.exists(self.local_save_dir):
            os.makedirs(self.local_save_dir)

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

    def parse_story_content(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        post_body = soup.select_one('.post-body')
        if not post_body:
            return ""

        for tag in post_body.find_all(['script', 'style']):
            tag.decompose()
        for div in post_body.find_all('div', class_='clear'):
            div.decompose()

        return post_body.get_text(strip=True)

    def sanitize_filename(self, filename):
        """移除文件名中的非法字符"""
        illegal_chars = r'[<>:"/\\|?*\x00-\x1f]'
        cleaned = re.sub(illegal_chars, '', filename)
        cleaned = cleaned.strip('. ')
        if not cleaned:
            cleaned = 'unnamed'
        return cleaned

    def save_as_txt(self, story_data):
        """将故事保存为本地txt文件"""
        title = story_data.get('title', 'unknown')
        category = story_data.get('category', '未分类')
        content = story_data.get('content', '')
        
        category_dir = os.path.join(self.local_save_dir, self.sanitize_filename(category))
        os.makedirs(category_dir, exist_ok=True)
        
        safe_title = self.sanitize_filename(title)
        txt_path = os.path.join(category_dir, f"{safe_title}.txt")
        
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"{title}\n")
            f.write(f"{'=' * 50}\n")
            f.write(f"{content}\n")
        
        logger.info(f"已保存为本地文件: {txt_path}")
        return txt_path

    def process_story(self, story):
        story_url = story['story_url']
        story_id = story['_id']

        logger.info(f"Processing: {story['title']}")

        try:
            html = self.fetch_page(story_url)
            if not html:
                logger.warning(f"  Failed to fetch: {story_url}")
                return False

            content = self.parse_story_content(html)
            if not content:
                logger.warning(f"  No content found: {story['title']}")
                return False

            # 尝试更新到 MongoDB
            try:
                self.collection.update_one(
                    {'_id': story_id},
                    {'$set': {
                        'content': content,
                        'content_length': len(content),
                        'content_fetched': True,
                        'fetched_at': datetime.now()
                    }}
                )
            except DocumentTooLarge:
                logger.warning(f"文档过大，保存为本地txt文件: {story['title']}")
                self.save_as_txt({
                    'title': story['title'],
                    'category': story.get('category', '未分类'),
                    'content': content
                })
                # 仍然标记为已获取，但内容字段置空
                self.collection.update_one(
                    {'_id': story_id},
                    {'$set': {
                        'content': '',
                        'content_length': len(content),
                        'content_fetched': True,
                        'saved_as_txt': True,
                        'fetched_at': datetime.now()
                    }}
                )

            logger.info(f"Done: {story['title']} ({len(content)} chars)")
            return True

        except Exception as e:
            logger.error(f"Error: {story['title']} - {e}")
            return False

    def run(self):
        logger.info("Short Story Fetcher Started")

        while True:
            try:
                story = self.collection.find_one_and_update(
                    {'content_fetched': False},
                    {'$set': {'claimed_at': datetime.now()}},
                    return_document=pymongo.ReturnDocument.AFTER
                )

                if not story:
                    logger.info("No pending stories, waiting...")
                    time.sleep(10)
                    continue

                self.process_story(story)

                time.sleep(random.uniform(0.1, 0.5))

            except KeyboardInterrupt:
                logger.info("Stopped")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                time.sleep(5)

        self.mongo_client.close()


if __name__ == '__main__':
    fetcher = ShortStoryFetcher()
    fetcher.run()
