#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import requests
import feedparser
import sqlite3
import time
from datetime import datetime
from pathlib import Path

# ============= НАСТРОЙКИ =============
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
HF_TOKEN = os.getenv("HF_API_TOKEN")
RSS_URL = "https://lenta.ru/rss/news/world"
DB_PATH = "data/sent.db"
TOP_N = 3  # Сколько новостей отправлять за раз

os.makedirs("data", exist_ok=True)

# ============= БД =============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent (
            url TEXT PRIMARY KEY,
            title TEXT,
            t TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def was_sent(url):
    try:
        conn = sqlite3.connect(DB_PATH)
        r = conn.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone()
        conn.close()
        return r is not None
    except:
        return False

def mark_sent(url, title):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, datetime())", (url, title))
        conn.commit()
        conn.close()
    except:
        pass

# ============= RSS =============
def fetch_news():
    """Загрузить свежие новости"""
    print("📡 Загружаю RSS...")
    try:
        resp = requests.get(RSS_URL, timeout=10)
        if resp.status_code != 200:
            print(f"❌ RSS статус: {resp.status_code}")
            return []
        
        feed = feedparser.parse(resp.content)
        articles = []
        
        for entry in feed.entries[:50]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            desc = entry.get("summary", "")
            
            if not title or not link or len(desc) < 20:
                continue
            if was_sent(link):
                continue
            
            articles.append({
                "title": title,
                "desc": desc[:300],
                "url": link
            })
        
        print(f"✅ Найдено новых: {len(articles)}")
        return articles
    except Exception as e:
        print(f"❌ Ошибка RSS: {e}")
        return []

# ============= QWEN РАНЖИРОВАНИЕ =============
def qwen_rank(articles, n=TOP_N):
    """Qwen выбирает топ-N новостей"""
    if len(articles) <= n:
        return articles
    
    print(f"🧠 Qwen выбирает топ-{n} из {len(articles)}...")
    
    # Формируем список для Qwen
    lst = [f"{i+1}) {a['title']}\n{a['desc'][:100]}" for i, a in enumerate(articles[:15])]
    prompt = f"Выбери {n} самых важных мировых новостей. Ответь только номерами через запятую (например: 1,3,5)\n\n" + "\n\n".join(lst)
    
    try:
        resp = requests.post(
            "https://api-inference.huggingface.co/models/Qwen/Qwen2-7B-Instruct",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 15,
                    "temperature": 0.3
                }
            },
            timeout=30
        )
        
        if resp.status_code == 200:
            text = resp.json()[0].get("generated_text", "")
            # Извлекаем номера
            nums = []
            for s in text.replace(",", " ").split():
                if s.strip().isdigit():
                    idx = int(s.strip()) - 1
                    if 0 <= idx < len(articles):
                        nums.append(idx)
            
            if nums:
                result = [articles[i] for i in nums[:n]]
                print(f"✅ Qwen выбрал {len(result)} новостей")
                return result
    except Exception as e:
        print(f"⚠️  Qwen ошибка: {e}")
    
    return articles[:n]

# ============= TELEGRAM =============
def send_to_telegram(title, url):
    """Отправить в Telegram"""
    msg = f"📰 <b>{title[:80]}</b>\n\n🔗 <a href=\"{url}\">Читать полностью</a>"
    
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },
            timeout=10
        )
        if resp.status_code == 200:
            print(f"✅ Отправлено: {title[:50]}...")
            return True
        else:
            print(f"❌ Telegram: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return False

# ============= MAIN =============
def main():
    print("\n" + "="*60)
    print("🤖 LENTA WORLD BOT + QWEN")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # Проверка
    if not TG_TOKEN or not TG_CHAT_ID:
        print("❌ Нет TG_TOKEN или TG_CHAT_ID")
        sys.exit(1)
    
    # Инициализация
    init_db()
    
    # Загрузить новости
    articles = fetch_news()
    if not articles:
        print("❌ Нет новых новостей")
        sys.exit(1)
    
    # Ранжирование Qwen
    ranked = qwen_rank(articles, TOP_N)
    
    # Отправить
    sent = 0
    for art in ranked:
        if send_to_telegram(art["title"], art["url"]):
            mark_sent(art["url"], art["title"])
            sent += 1
            time.sleep(2)
    
    print("\n" + "="*60)
    print(f"📊 Отправлено: {sent}/{len(ranked)}")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
