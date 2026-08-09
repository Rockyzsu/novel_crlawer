# 海棠小说爬虫

## 功能说明

爬取 https://www.3haitang.com/ 网站的小说数据，包括：

- 8个分类：耽美小说、百合小说、言情小说、高辣文、腹黑小说、种田文、其他类型、全本
- 每个分类的小说列表
- 每部小说的所有章节内容

## 数据存储

使用 MongoDB 存储数据，结构如下：

```json
{
  "title": "小说标题",
  "novel_url": "小说链接",
  "category": "分类",
  "chapters": [
    {
      "title": "章节标题",
      "content": "章节内容"
    }
  ],
  "completed": true
}
```

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行爬虫

```bash
python crawler.py
```

## 注意事项

1. 需要先安装并启动 MongoDB
2. 爬虫会自动跳过已存在的小说
3. 请控制爬取速度，避免对目标网站造成压力
