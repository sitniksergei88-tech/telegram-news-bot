#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
import feedparser
from datetime import datetime
from openai import OpenAI
import time
import os
import sqlite3

# ============= КОНФИГ =============
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
GNEWS_KEY = os.getenv("GNEWS_KEY")
PERPLEXITY_KEY = os.getenv("PERPLEXITY_KEY")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

USE_PERPLEXITY_SUMMARY = True
INTERVAL_BETWEEN_POSTS = 30
ARTICLES_TO_SEND = 50
DB = "data/sent_links.db"

# ============= PERPLEXITY =============

def create_perplexity_client():
    return OpenAI(
        api_key=PERPLEXITY_KEY,
        base_url="https://api.perplexity.ai"
    )

# ============= ЛОГИРОВАНИЕ =============

def safe_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

def log_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

# ============= БД =============

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent (
            url TEXT PRIMARY KEY,
            title TEXT,
            time TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def was_sent(url):
    conn = sqlite3.connect(DB)
    result = conn.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone()
    conn.close()
    return result is not None

def mark_sent(url, title):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, ?)", 
                 (url, title, datetime.now()))
    conn.commit()
    conn.close()

# ============= NEWSAPI =============

def fetch_newsapi(articles_list):
    categories = ["business", "technology", "science", "health", "entertainment", "general"]
    safe_log("📰 NewsAPI...")
    
    for category in categories:
        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "country": "ru",
                "category": category,
                "apiKey": NEWSAPI_KEY,
                "sortBy": "publishedAt",
                "pageSize": 8
            }
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if data.get("status") == "ok":
                for article in data.get("articles", []):
                    if article.get("title") and article.get("url") and not was_sent(article.get("url")):
                        articles_list.append({
                            "title": article.get("title"),
                            "description": article.get("description"),
                            "url": article.get("url"),
                            "source": f"NewsAPI ({category})",
                        })
                safe_log(f"  ✓ {category}: +{len(data.get('articles', []))}")
            time.sleep(1)
        except Exception as e:
            safe_log(f"  ✗ {category}: {e}")
    
    return articles_list

# ============= GNEWS =============

def fetch_gnews(articles_list):
    queries = ["новости", "технология", "бизнес", "спорт", "наука", "здоровье"]
    safe_log("🔍 GNews...")
    
    for query in queries:
        try:
            url = "https://gnews.io/api/v4/search"
            params = {
                "q": query,
                "country": "ru",
                "apikey": GNEWS_KEY,
                "max": 6,
                "lang": "ru",
                "sortby": "publishedAt"
            }
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if data.get("articles"):
                for article in data.get("articles", []):
                    if article.get("title") and article.get("url") and not was_sent(article.get("url")):
                        articles_list.append({
                            "title": article.get("title"),
                            "description": article.get("description"),
                            "url": article.get("url"),
                            "source": f"GNews ({query})",
                        })
                safe_log(f"  ✓ {query}: +{len(data.get('articles', []))}")
            time.sleep(1)
        except Exception as e:
            safe_log(f"  ✗ {query}: {e}")
    
    return articles_list

# ============= RSS =============

def fetch_rss(articles_list):
    rss_feeds = [
        ("Lenta.ru", "https://lenta.ru/rss"),
        ("RBC", "https://rbc.ru/rbc/news/rssfull"),
        ("BBC Russian", "https://www.bbc.com/russian/index.xml"),
        ("Interfax", "https://rss.interfax.ru/politics/"),
        ("Meduza", "https://meduza.io/rss/all"),
        ("TASS", "https://tass.ru/rss/v2.xml"),
        ("Kommersant", "https://www.kommersant.ru/RSS/news.xml"),
    ]
    
    safe_log("🌐 RSS...")
    
    for source_name, feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")[:300]
                
                if title and link and not was_sent(link):
                    articles_list.append({
                        "title": title,
                        "description": summary,
                        "url": link,
                        "source": f"RSS ({source_name})",
                    })
            safe_log(f"  ✓ {source_name}: +{min(len(feed.entries), 3)}")
        except Exception as e:
            safe_log(f"  ✗ {source_name}: {e}")
    
    return articles_list

# ============= ДЕДУПЛИКАЦИЯ =============

def deduplicate_articles(articles):
    safe_log(f"🔄 Дедупликация...")
    
    seen = set()
    unique = []
    
    for article in articles:
        url = article.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(article)
    
    safe_log(f"   → {len(unique)} уникальных")
    return unique

# ============= AI СУММАРИЗАЦИЯ =============

def summarize_with_perplexity(articles, limit=None):
    if limit:
        articles = articles[:limit]
    
    safe_log(f"🤖 Perplexity: {len(articles)}...\n")
    
    client = create_perplexity_client()
    summarized = []
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "")
        desc = article.get("description", "")
        
        if not title or not desc:
            article["summary"] = desc if desc else title
            summarized.append(article)
            continue
        
        prompt = f"""Напиши краткую сводку (1-2 предложения) для Telegram:

Заголовок: {title}
Описание: {desc}

Требования:
- Максимум 2 предложения
- Добавь 1-2 эмодзи
- Не повторяй заголовок
- Будь информативным"""
        
        try:
            response = client.chat.completions.create(
                model="sonar",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.7
            )
            
            article["summary"] = response.choices[0].message.content
            safe_log(f"  [{i}/{len(articles)}] ✓ {title[:40]}...")
            
        except Exception as e:
            safe_log(f"  [{i}/{len(articles)}] ✗ {e}")
            article["summary"] = desc[:150] if desc else title
        
        summarized.append(article)
        time.sleep(0.2)
    
    return summarized

# ============= TELEGRAM =============

def send_to_telegram(articles, limit=None):
    if limit:
        articles = articles[:limit]
    
    log_section(f"📤 ОТПРАВКА {len(articles)} ПОСТОВ")
    
    telegram_url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    
    sent = 0
    failed = 0
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "")[:50]
        summary = article.get("summary", article.get("description", ""))[:500]
        url = article.get("url", "")
        source = article.get("source", "Источник")
        
        if not url:
            continue
        
        message = f"""*{title}*

{summary}

🔗 [{source}]({url})"""
        
        try:
            response = requests.post(
                telegram_url,
                json={
                    "chat_id": TG_CHAT_ID,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False
                },
                timeout=10
            )
            
            if response.status_code == 200:
                safe_log(f"[{i}/{len(articles)}] ✓ {title}...")
                mark_sent(url, title)
                sent += 1
            else:
                safe_log(f"[{i}/{len(articles)}] ✗ HTTP {response.status_code}")
                failed += 1
        
        except Exception as e:
            safe_log(f"[{i}/{len(articles)}] ✗ {e}")
            failed += 1
        
        if i < len(articles):
            time.sleep(INTERVAL_BETWEEN_POSTS)
    
    log_section("✨ РЕЗУЛЬТАТ")
    safe_log(f"✅ Успешно: {sent}")
    if failed > 0:
        safe_log(f"❌ Ошибок: {failed}")
    
    return sent, failed

# ============= MAIN =============

def main():
    log_section("🚀 TELEGRAM NEWS BOT")
    
    keys = {
        "NEWSAPI_KEY": NEWSAPI_KEY,
        "GNEWS_KEY": GNEWS_KEY,
        "PERPLEXITY_KEY": PERPLEXITY_KEY,
        "TG_TOKEN": TG_TOKEN,
        "TG_CHAT_ID": TG_CHAT_ID
    }
    
    for key_name, key_value in keys.items():
        if not key_value:
            safe_log(f"❌ ОШИБКА: {key_name} не установлен!")
            return
    
    safe_log("✓ Все ключи готовы")
    
    init_db()
    
    log_section("ЭТАП 1: СБОР")
    articles = []
    articles = fetch_newsapi(articles)
    articles = fetch_gnews(articles)
    articles = fetch_rss(articles)
    articles = deduplicate_articles(articles)
    
    if not articles:
        safe_log("❌ Новостей не найдено!")
        return
    
    safe_log(f"✓ Собрано: {len(articles)}")
    
    log_section("ЭТАП 2: СУММАРИЗАЦИЯ")
    articles = summarize_with_perplexity(articles, limit=ARTICLES_TO_SEND)
    
    log_section("ЭТАП 3: ОТПРАВКА")
    sent, failed = send_to_telegram(articles, limit=ARTICLES_TO_SEND)
    
    log_section("✨ ГОТОВО")
    safe_log(f"Отправлено: {sent} постов")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        safe_log(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
