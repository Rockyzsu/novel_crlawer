import time
import random
import os
import re
import logging
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urljoin, quote
import pymongo
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv()

log_dir = 'logs'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_file = os.path.join(log_dir, f'short_story_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ShortStoryCrawler:
    """爬取 blog.xbookcn.net 短篇情色小说列表"""
    
    BASE_URL = "https://blog.xbookcn.net"
    
    # 短篇情色小说分类
    CATEGORIES = {
        # "精选作品": "精选作品",
        # "现代情色": "现代情色",
        # "日本情色": "日本情色",
        # "西洋情色": "西洋情色",
        # "伴侣交换": "伴侣交换",
        # "武侠情色": "武侠情色",
        # "奇幻科幻": "奇幻科幻",
        # "家庭乱伦": "家庭乱伦",
        # "性爱调教": "性爱调教",
        # "粗野性交": "粗野性交",
        # "多人群交": "多人群交",
        # "教师学生": "教师学生",
        # "古典情色": "古典情色",
        # "历史情色": "历史情色",
        # "同性情色": "同性情色",
        # "都市生活": "都市生活",
        # "乡间记趣": "乡间记趣",
        # "疯狂暴露": "疯狂暴露",
        # "午夜怪谈": "午夜怪谈",
        # "游戏乐园": "游戏乐园",
        # "医生护士": "医生护士",
        # "奇遇物语": "奇遇物语",
        # "左邻右舍": "左邻右舍",
        # "同事之间": "同事之间",
        # "旅游纪事": "旅游纪事",
        # "纯洁恋情": "纯洁恋情",
        # "明星系列": "明星系列",

        # "意外收获": "意外收获",
        # "忘年之乐": "忘年之乐",
        # "另类其他": "另类其他",
        # "知识技巧": "知识技巧",
        # 情色小说分类
        # "经典激情": "经典激情",
        # "近亲乱伦": "近亲乱伦",
        # "人妻美妇": "人妻美妇",
        # "学生校园": "学生校园",
        # "职业制服": "职业制服",
        # "粗暴性爱": "粗暴性爱",
        # "情色武侠": "情色武侠",
        # "情欲性爱": "情欲性爱",
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
        print('mongo connected')
        self.db = self.client[mongo_db]
        self.collection = self.db['xbookcn_short_stories']

        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')

        self.driver = uc.Chrome(options=chrome_options, version_main=151)
        self.wait = WebDriverWait(self.driver, 20)
        print('updated')

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

    def load_existing_stories(self):
        """加载已爬取的小说URL"""
        existing = set()
        for doc in self.collection.find({}, {'story_url': 1}):
            existing.add(doc['story_url'])
        return existing

    def get_category_stories(self, category_name, category_label):
        """获取分类下的所有故事链接（支持分页）"""
        encoded_label = quote(category_label)
        url = f"{self.BASE_URL}/search/label/{encoded_label}"
        logger.info(f"[Category] {category_name}: {url}")
        
        stories = []
        page = 1
        
        while url:
            html = self.get_page(url)
            if not html:
                break
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # 提取文章列表
            for post in soup.select('.post'):
                title_el = post.select_one('.post-title a')
                if not title_el:
                    continue
                
                href = title_el.get('href', '')
                title = title_el.get_text(strip=True)
                
                if href and title:
                    if not href.startswith('http'):
                        href = urljoin(self.BASE_URL, href)
                    stories.append({'title': title, 'url': href})
            
            # 查找下一页链接
            older = soup.select_one('.blog-pager-older-link')
            if older and older.get('href'):
                url = older['href']
                page += 1
                logger.info(f"    Next page {page}")
                self.sleep_random(1.0, 2.0)
            else:
                url = None
        
        return stories

    def save_story_meta(self, story_title, story_url, category, existing_stories):
        """保存故事元数据到MongoDB（不含内容）"""
        if story_url in existing_stories:
            return False
        
        story_data = {
            "title": story_title,
            "story_url": story_url,
            "category": category,
            "content": "",
            "content_fetched": False,
            "crawled_at": datetime.now(),
        }
        
        self.collection.insert_one(story_data)
        existing_stories.add(story_url)
        return True

    def run(self, categories=None):
        """运行爬虫 - 只爬取列表，不爬取内容"""
        logger.info("=" * 60)
        logger.info("XbookCn Short Story Crawler (List Only)")
        logger.info("=" * 60)

        try:
            existing_stories = self.load_existing_stories()
            logger.info(f"已存在 {len(existing_stories)} 篇小说，跳过爬取")

            target_categories = categories if categories else self.CATEGORIES
            
            for cat_name, cat_label in target_categories.items():
                logger.info(f"\n{'='*50}")
                logger.info(f"Category: {cat_name}")
                logger.info(f"{'='*50}")

                stories = self.get_category_stories(cat_name, cat_label)
                logger.info(f"  Found {len(stories)} stories in {cat_name}")

                saved_count = 0
                for i, story in enumerate(stories):
                    if self.save_story_meta(story["title"], story["url"], cat_name, existing_stories):
                        saved_count += 1
                        logger.info(f"  [{i+1}/{len(stories)}] Saved: {story['title']}")
                    else:
                        logger.debug(f"  [{i+1}/{len(stories)}] Skip: {story['title']}")
                    
                    self.sleep_random(0.1, 0.3)

                logger.info(f"  Category {cat_name}: saved {saved_count} new stories")
                self.sleep_random(2.0, 4.0)

        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            self.driver.quit()
            self.client.close()
            logger.info("\nDone.")


if __name__ == "__main__":
    import sys
    
    crawler = ShortStoryCrawler()
    
    if len(sys.argv) > 1:
        selected_categories = {}
        for cat_name in sys.argv[1:]:
            if cat_name in crawler.CATEGORIES:
                selected_categories[cat_name] = crawler.CATEGORIES[cat_name]
            else:
                logger.warning(f"Unknown category: {cat_name}")
        
        if selected_categories:
            crawler.run(categories=selected_categories)
        else:
            logger.error("No valid categories specified")
    else:
        crawler.run()
