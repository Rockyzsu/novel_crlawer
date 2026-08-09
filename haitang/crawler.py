import requests
from bs4 import BeautifulSoup
import pymongo
import time
import random
from urllib.parse import urljoin
import re
import os
from dotenv import load_dotenv

load_dotenv()

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
        self.collection = self.db['novels']
        
    def get_page(self, url):
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'gbk'
            return response.text
        except Exception as e:
            print(f"Error: {url} - {e}")
            return None
    
    def parse_categories(self):
        return {
            '耽美小说': 'xs/1-default-0-0-0-0-0-0-{}.html',
            '百合小说': 'xs/2-default-0-0-0-0-0-0-{}.html',
            '言情小说': 'xs/3-default-0-0-0-0-0-0-{}.html',
            '高辣文': 'xs/4-default-0-0-0-0-0-0-{}.html',
            '腹黑小说': 'xs/5-default-0-0-0-0-0-0-{}.html',
            '种田文': 'xs/6-default-0-0-0-0-0-0-{}.html',
            '其他类型': 'xs/7-default-0-0-0-0-0-0-{}.html',
            '全本': 'xs/10-default-0-0-0-0-0-0-{}.html'
        }
    
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
    
    def save_novel(self, novel_data):
        self.collection.insert_one(novel_data)
    
    def crawl_novel(self, novel_url):
        html = self.get_page(novel_url)
        if not html:
            return []
        
        chapters = self.parse_novel_detail(html, novel_url)
        chapter_contents = []
        
        for i, chapter in enumerate(chapters):
            print(f"  Chapter {i+1}/{len(chapters)}: {chapter['title']}")
            chapter_html = self.get_page(chapter['url'])
            if chapter_html:
                content = self.parse_chapter_content(chapter_html)
                chapter_contents.append({
                    'title': chapter['title'],
                    'content': content
                })
            time.sleep(random.uniform(0.5, 1.5))
        
        return chapter_contents
    
    def run(self):
        categories = self.parse_categories()
        
        for category_name, category_pattern in categories.items():
            print(f"\nCategory: {category_name}")
            page = 1
            
            while True:
                url = urljoin(self.base_url, category_pattern.format(page))
                print(f"Page {page}: {url}")
                html = self.get_page(url)
                if not html:
                    break
                
                novels = self.parse_novel_list(html)
                if not novels:
                    break
                
                if page == 1:
                    total_pages = self.get_total_pages(html)
                    print(f"Total pages: {total_pages}")
                
                for novel in novels:
                    if self.novel_exists(novel['url']):
                        print(f"Skip existing: {novel['title']}")
                        continue
                    
                    print(f"Crawling: {novel['title']}")
                    chapters = self.crawl_novel(novel['url'])
                    
                    if chapters:
                        novel_data = {
                            'title': novel['title'],
                            'novel_url': novel['url'],
                            'category': category_name,
                            'chapters': chapters,
                            'completed': True
                        }
                        self.save_novel(novel_data)
                        print(f"Saved: {novel['title']} ({len(chapters)} chapters)")
                    
                    time.sleep(random.uniform(1, 2))
                
                if page >= total_pages:
                    break
                page += 1
                time.sleep(random.uniform(1, 2))

if __name__ == '__main__':
    crawler = NovelCrawler()
    crawler.run()
