import requests
from bs4 import BeautifulSoup
import pymongo
import time
import random
from urllib.parse import urljoin
import re
import os
from dotenv import load_dotenv
import logging
from datetime import datetime

load_dotenv()

# 配置日志
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

class NovelCrawler:
    def __init__(self):
        self.base_url = "https://www.3haitang.com/"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        mongo_host = os.getenv('MONGO_HOST', 'localhost')
        mongo_port = int(os.getenv('MONGO_PORT', '27017'))
        mongo_user = os.getenv('MONGO_USER', '')
        mongo_pass = os.getenv('MONGO_PASS', '')
        mongo_db = os.getenv('MONGO_DB', 'novel_db')
        
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
        self.collection = self.db['haitang']
        
    def get_page(self, url, max_retries=10):
        """获取页面内容，支持重试机制，间隔指数递增"""
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=self.headers, timeout=10)
                response.encoding = 'gbk'
                return response.text
            except Exception as e:
                wait_time = (2 ** attempt) + random.uniform(0, 1)  # 指数退避: 1,2,4,8,16,32,64,128,256,512秒
                logger.warning(f"请求失败 (第{attempt+1}/{max_retries}次): {url} - {e}, {wait_time:.1f}秒后重试")
                if attempt < max_retries - 1:
                    time.sleep(wait_time)
                else:
                    logger.error(f"请求最终失败: {url} - 已达最大重试次数")
        return None
    
    def parse_categories(self):
        """返回分类配置，包含连载中(1)和已完成(2)"""
        categories = {
            '耽美小说': '1',
            '百合小说': '2',
            '言情小说': '3',
            '高辣文': '4',
            '腹黑小说': '5',
            '种田文': '6',
            '其他类型': '7',
            '全本': '10'
        }
        return categories
    
    def get_category_url(self, category_id, status, page):
        """构建分类URL
        status: 0=全部, 1=连载中, 2=已完成
        """
        return f'xs/{category_id}-default-0-0-0-0-{status}-0-{page}.html'
    
    def parse_novel_list(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        novels = []
        
        sitebox = soup.find('div', class_='sitebox')
        if sitebox:
            for dl in sitebox.find_all('dl'):
                # Title is in dd > h3 > a
                h3 = dl.find('h3')
                if h3:
                    link = h3.find('a')
                    if link and link.get('href'):
                        novel_url = link.get('href')
                        if not novel_url.startswith('http'):
                            novel_url = urljoin(self.base_url, novel_url)
                        title = link.get_text(strip=True)
                        if title:
                            novels.append({'title': title, 'url': novel_url})
        
        return novels
    
    def get_total_pages(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        pagelink = soup.find('div', class_='pagelink')
        if pagelink:
            links = pagelink.find_all('a')
            max_page = 1
            for link in links:
                text = link.get_text(strip=True)
                if text.isdigit():
                    max_page = max(max_page, int(text))
            return max_page
        return 1
    
    def parse_novel_detail(self, html, novel_url):
        soup = BeautifulSoup(html, 'html.parser')
        chapters = []
        
        book_list = soup.find('div', class_='book_list')
        if book_list:
            for link in book_list.find_all('a'):
                href = link.get('href', '')
                if href:
                    if not href.startswith('http'):
                        chapter_url = urljoin(novel_url, href)
                    else:
                        chapter_url = href
                    chapter_title = link.get_text(strip=True)
                    if chapter_title:
                        chapters.append({'title': chapter_title, 'url': chapter_url})
        
        return chapters
    
    def parse_chapter_content(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        content_div = soup.find('div', id='htmlContent')
        if content_div:
            for script in content_div.find_all(['script', 'div']):
                script.decompose()
            return content_div.get_text(strip=True)
        return ""
    
    def novel_exists(self, novel_url):
        return self.collection.find_one({'novel_url': novel_url}) is not None
    
    def load_existing_novels(self):
        """加载所有已爬取的小说URL到内存，避免重复请求"""
        existing = set()
        for doc in self.collection.find({}, {'novel_url': 1}):
            existing.add(doc['novel_url'])
        return existing
    
    def save_novel(self, novel_data):
        self.collection.insert_one(novel_data)
    
    def crawl_novel(self, novel_url):
        html = self.get_page(novel_url)
        if not html:
            return []
        
        chapters = self.parse_novel_detail(html, novel_url)
        chapter_contents = []
        
        for i, chapter in enumerate(chapters):
            logger.info(f"  章节 {i+1}/{len(chapters)}: {chapter['title']}")
            chapter_html = self.get_page(chapter['url'])
            
            content = None
            if chapter_html:
                content = self.parse_chapter_content(chapter_html)
                logger.debug(f"    章节获取成功: {chapter['title']}")
            else:
                logger.error(f"    章节获取失败: {chapter['title']}")
            
            chapter_contents.append({
                'title': chapter['title'],
                'url': chapter['url'],
                'content': content
            })
            
            time.sleep(random.uniform(0.5, 1.5))
        
        return chapter_contents
    
    def run(self):
        categories = self.parse_categories()
        
        # 启动时加载所有已爬取的小说URL
        existing_novels = self.load_existing_novels()
        logger.info(f"已存在 {len(existing_novels)} 部小说，跳过爬取")
        
        status_map = {'1': '连载中', '2': '已完成'}
        
        for category_name, category_id in categories.items():
            logger.info(f"开始爬取分类: {category_name}")
            
            for status_code, status_name in status_map.items():
                logger.info(f"  状态: {status_name}")
                page = 1
                
                while True:
                    url = urljoin(self.base_url, self.get_category_url(category_id, status_code, page))
                    logger.info(f"  爬取页面: {url}")
                    html = self.get_page(url)
                    if not html:
                        break
                    
                    novels = self.parse_novel_list(html)
                    if not novels:
                        break
                    
                    if page == 1:
                        total_pages = self.get_total_pages(html)
                        logger.info(f"  总页数: {total_pages}")
                    
                    for novel in novels:
                        if novel['url'] in existing_novels:
                            logger.info(f"  跳过已存在: {novel['title']}")
                            continue
                        
                        logger.info(f"  爬取小说: {novel['title']}")
                        chapters = self.crawl_novel(novel['url'])
                        
                        if chapters:
                            novel_data = {
                                'title': novel['title'],
                                'novel_url': novel['url'],
                                'category': category_name,
                                'status': status_name,
                                'chapter_count': len(chapters),
                                'crawled_at': datetime.now(),
                                'chapters': chapters
                            }
                            self.save_novel(novel_data)
                            existing_novels.add(novel['url'])
                            logger.info(f"  保存完成: {novel['title']} ({len(chapters)} 章)")
                        
                        time.sleep(random.uniform(1, 2))
                    
                    if page >= total_pages:
                        break
                    page += 1
                    time.sleep(random.uniform(1, 2))
        
        logger.info("爬取任务完成")

if __name__ == '__main__':
    crawler = NovelCrawler()
    crawler.run()
