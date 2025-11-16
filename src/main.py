#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import feedparser
import time
import os
import sqlite3
import re

HF_TOKEN = os.getenv("HF_API_TOKEN")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

RSS_URL = "https://lenta.ru/rss"
MAX_TOP_ARTICLES = 5
DB = "data/sent_links.db"

def safe_log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent (
            url TEXT PRIMARY KEY, title TEXT, summary TEXT, time TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def was_sent(url):
    conn = sqlite3.connect(DB)
    result = conn.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone()
    conn.close()
    return result is not None

def mark_sent(url, title, summary):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, ?, ?)", 
                 (url, title, summary, time.strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()

def fetch_lenta():
    safe_log("📰 Загрузка Lenta...")
    feed = feedparser.parse(RSS_URL)
    articles = []
    
    for entry in feed.entries[:100]:
        title = re.sub(r'\d+$', '', entry.get("title", "")).strip()
        link = entry.get("link", "").strip()
        desc = re.sub(r'\d+$', '', entry.get("summary", "")[:300]).strip()
        
        if not title or not link or len(desc) < 20 or was_sent(link):
            continue
        
        image = None
        if hasattr(entry, 'media_content') and entry.media_content:
            image = entry.media_content[0].get('url')
        
        articles.append({"title": title, "desc": desc, "url": link, "image": image})
    
    safe_log(f"✓ Найдено: {len(articles)} новых")
    return articles

def send_tg(title, text, image, rating):
    stars = "⭐" * (rating // 2)
    msg = f"*{title}*\n\n{text}\n\n{stars}"
    
    try:
        if image:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                json={"chat_id": TG_CHAT_ID, "photo": image, "caption": msg, "parse_mode": "Markdown"},
                timeout=10)
        else:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
                timeout=10)
        return True
    except:
        return False

def main():
    safe_log("🚀 LENTA → TELEGRAM (HuggingFace)")
    
    if not all([HF_TOKEN, TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ НЕТ КЛЮЧЕЙ!")
        return
    
    init_db()
    articles = fetch_lenta()
    
    if not articles:
        safe_log("ℹ️ НЕТ НОВОСТЕЙ")
        return
    
    safe_log(f"✅ Отправляю {min(len(articles), MAX_TOP_ARTICLES)}...")
    for art in articles[:MAX_TOP_ARTICLES]:
        if send_tg(art["title"], art["desc"], art["image"], 7):
            mark_sent(art["url"], art["title"], art["desc"])
            safe_log(f"✓ {art['title'][:40]}...")
            time.sleep(5)
    
    safe_log("✨ ГОТОВО!")

if __name__ == "__main__":
    main()
