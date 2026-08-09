import pychrome
import time
import random
import json
import pymongo


class XbookCnCrawler:
    BASE_URL = "https://book.xbookcn.net"
    CDP_URL = "http://127.0.0.1:9222"

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
        self.client = pymongo.MongoClient("localhost", 27017)
        self.db = self.client["xbookcn_db"]
        self.collection = self.db["novels"]
        self.browser = None
        self.tab = None

    def connect_browser(self):
        self.browser = pychrome.Browser(url=self.CDP_URL)
        self.tab = self.browser.list_tab()[0]
        self.tab.start()
        self.tab.Page.enable()

    def close_browser(self):
        if self.tab:
            try:
                self.tab.stop()
            except Exception:
                pass

    def navigate(self, url, wait=6):
        self.tab.Page.navigate(url=url)
        time.sleep(wait)

    def js_eval(self, expression):
        result = self.tab.Runtime.evaluate(expression=expression)
        return result.get("result", {}).get("value", "")

    def sleep_random(self, lo=1.0, hi=3.0):
        time.sleep(random.uniform(lo, hi))

    def novel_exists(self, novel_url):
        return self.collection.find_one({"novel_url": novel_url}) is not None

    def get_category_novels(self, category_name, category_path):
        url = self.BASE_URL + category_path
        print(f"  [Category] {category_name}: {url}")
        self.navigate(url, wait=6)

        js = """(function() {
            var posts = document.querySelectorAll('.post');
            var novels = [];
            for (var i = 0; i < posts.length; i++) {
                var body = posts[i].querySelector('.post-body');
                if (!body) continue;
                var links = body.querySelectorAll('a');
                for (var j = 0; j < links.length; j++) {
                    var href = links[j].href;
                    var text = links[j].innerText.trim();
                    if (href && text && href.includes('/search/label/')) {
                        novels.push({title: text, url: href});
                    }
                }
            }
            return JSON.stringify(novels);
        })()"""
        raw = self.js_eval(js)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    def get_novel_chapters(self, novel_url):
        self.navigate(novel_url, wait=6)

        chapters = []
        page = 1
        while True:
            js = """(function() {
                var posts = document.querySelectorAll('.post');
                var chapters = [];
                for (var i = 0; i < posts.length; i++) {
                    var titleEl = posts[i].querySelector('.post-title a');
                    if (!titleEl) continue;
                    chapters.push({
                        title: titleEl.innerText.trim(),
                        url: titleEl.href
                    });
                }
                return JSON.stringify(chapters);
            })()"""
            raw = self.js_eval(js)
            if raw:
                try:
                    page_chapters = json.loads(raw)
                    chapters.extend(page_chapters)
                except Exception:
                    pass

            has_next = self.js_eval("""(function() {
                var older = document.querySelector('.blog-pager-older-link');
                return older ? older.href : '';
            })()""")

            if not has_next:
                break
            page += 1
            print(f"    Next page {page}: {has_next}")
            self.navigate(has_next, wait=5)

        return chapters

    def get_chapter_content(self, chapter_url):
        self.navigate(chapter_url, wait=4)

        js = """(function() {
            var post = document.querySelector('.post-body');
            if (!post) return '';
            var clone = post.cloneNode(true);
            var scripts = clone.querySelectorAll('script, style, div.clear');
            for (var i = 0; i < scripts.length; i++) {
                scripts[i].remove();
            }
            return clone.innerText.trim();
        })()"""
        return self.js_eval(js)

    def crawl_novel(self, novel_title, novel_url, category=""):
        if self.novel_exists(novel_url):
            print(f"  [Skip] {novel_title} (already crawled)")
            return

        print(f"  [Crawl] {novel_title}: {novel_url}")
        chapters = self.get_novel_chapters(novel_url)
        if not chapters:
            print(f"    No chapters found, skipping.")
            return

        print(f"    Found {len(chapters)} chapters")
        chapter_data = []
        for i, ch in enumerate(chapters):
            print(f"    Chapter {i+1}/{len(chapters)}: {ch['title']}")
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
            "chapters": chapter_data,
            "chapter_count": len(chapter_data),
            "completed": True,
        }
        self.collection.insert_one(novel_data)
        print(f"    Saved: {novel_title} ({len(chapter_data)} chapters)")

    def run(self):
        print("=" * 60)
        print("XbookCn Novel Crawler")
        print("=" * 60)

        try:
            self.connect_browser()
            print("Connected to Chrome debug port.\n")

            for cat_name, cat_path in self.CATEGORIES.items():
                print(f"\n{'='*50}")
                print(f"Category: {cat_name}")
                print(f"{'='*50}")

                novels = self.get_category_novels(cat_name, cat_path)
                print(f"  Found {len(novels)} novels in {cat_name}")

                for i, novel in enumerate(novels):
                    print(f"\n  [{i+1}/{len(novels)}] {novel['title']}")
                    try:
                        self.crawl_novel(novel["title"], novel["url"], category=cat_name)
                    except Exception as e:
                        print(f"    Error: {e}")
                    self.sleep_random(2.0, 4.0)

                self.sleep_random(3.0, 5.0)

        except Exception as e:
            print(f"Fatal error: {e}")
        finally:
            self.close_browser()
            self.client.close()
            print("\nDone.")


if __name__ == "__main__":
    crawler = XbookCnCrawler()
    crawler.run()
