import pymongo
import os
import re
from dotenv import load_dotenv
import logging
from datetime import datetime

load_dotenv()

log_dir = 'logs'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_file = os.path.join(log_dir, f'short_story_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

EXPORT_DIR = os.getenv('LOCAL_SAVE_DIR', '/root/videos/novel/xbookcn_short')


def sanitize_filename(filename):
    """移除文件名中的非法字符"""
    illegal_chars = r'[<>:"/\\|?*\x00-\x1f]'
    cleaned = re.sub(illegal_chars, '', filename)
    cleaned = cleaned.strip('. ')
    if not cleaned:
        cleaned = 'unnamed'
    return cleaned


def connect_mongo():
    """连接 MongoDB"""
    mongo_host = os.getenv('MONGO_HOST', 'localhost')
    mongo_port = int(os.getenv('MONGO_PORT', '27017'))
    mongo_user = os.getenv('MONGO_USER', '')
    mongo_pass = os.getenv('MONGO_PASS', '')
    mongo_db = os.getenv('MONGO_DB', 'xbookcn_db')

    if mongo_user and mongo_pass:
        client = pymongo.MongoClient(
            host=mongo_host,
            port=mongo_port,
            username=mongo_user,
            password=mongo_pass
        )
    else:
        client = pymongo.MongoClient(mongo_host, mongo_port)

    db = client[mongo_db]
    return db['xbookcn_short_stories']


def export_stories():
    """导出已获取内容的短篇小说为 txt 文件"""
    collection = connect_mongo()

    # 查询内容已获取且未导出的小说
    query = {
        'content_fetched': True,
        'has_exported': {'$ne': True}
    }

    stories = collection.find(query).limit(50)

    success_count = 0
    fail_count = 0

    for story in stories:
        title = story.get('title', 'unknown')
        category = story.get('category', '未分类')
        content = story.get('content', '')

        # 跳过内容为空的（可能已保存为txt）
        if not content:
            logger.warning(f"  跳过无内容的小说: {title}")
            continue

        # 创建分类目录
        category_dir = os.path.join(EXPORT_DIR, sanitize_filename(category))
        os.makedirs(category_dir, exist_ok=True)

        # 生成文件名
        safe_title = sanitize_filename(title)
        txt_path = os.path.join(category_dir, f"{safe_title}.txt")

        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"{title}\n")
                f.write(f"{'=' * 50}\n")
                f.write(f"{content}\n")

            # 更新数据库标记为已导出
            collection.update_one(
                {'_id': story['_id']},
                {'$set': {'has_exported': True}}
            )

            success_count += 1
            logger.info(f"  导出成功: {title} -> {txt_path}")

        except Exception as e:
            fail_count += 1
            logger.error(f"  导出失败: {title} - {e}")

    logger.info(f"导出完成: 成功 {success_count} 篇, 失败 {fail_count} 篇")


if __name__ == '__main__':
    export_stories()
