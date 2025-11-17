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
    
    for entry in feed.entries[:50]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        desc = entry.get("summary", "")[:400].strip()
        
        title = re.sub(r'\d+$', '', title).strip()
        desc = re.sub(r'\d+$', '', desc).strip()
        
        image_url = None
        if hasattr(entry, 'media_content') and entry.media_content:
            image_url = entry.media_content[0].get('url')
        
        if not image_url and hasattr(entry, 'enclosures') and entry.enclosures:
            image_url = entry.enclosures[0].get('href')
        
        if not title or not link or len(desc) < 30:
            continue
        
        if was_sent(link):
            continue
        
        articles.append({
            "title": title,
            "desc": desc,
            "url": link,
            "image": image_url
        })
    
    safe_log(f"✓ Найдено: {len(articles)}")
    return articles

def rewrite_with_hf(title, text):
    """Переписывает текст с HuggingFace"""
    if not HF_TOKEN:
        return text[:150]
    
    prompt = f"""Переписи это новость в 2-3 предложениях. Не копируй исходный текст!

Заголовок: {title}
Текст: {text}

Ответ (ТОЛЬКО ПЕРЕПИСАННЫЙ ТЕКСТ):"""
    
    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 100,
                    "temperature": 0.8,
                    "do_sample": True
                }
            },
            timeout=20
        )
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                result = data[0].get("generated_text", "").strip()
                if prompt in result:
                    result = result.replace(prompt, "").strip()
                result = result.split('.')[0] + '.'
                result = re.sub(r'\d+$', '', result).strip()
                return result[:200] if len(result) > 10 else text[:150]
    except:
        pass
    
    return text[:150]

def download_image(url):
    """Загружает изображение"""
    if not url:
        return None
    
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            os.makedirs("data", exist_ok=True)
            filename = f"data/img_{int(time.time())}.jpg"
            with open(filename, 'wb') as f:
                f.write(response.content)
            return filename
    except:
        pass
    
    return None

def send_to_telegram(articles):
    if not articles:
        safe_log("⚠️ НЕТ НОВОСТЕЙ")
        return 0
    
    safe_log(f"📤 Отправляю {len(articles)}...\n")
    sent = 0
    
    for i, art in enumerate(articles, 1):
        title = art["title"]
        summary = rewrite_with_hf(title, art["desc"])
        
        image_path = None
        if art["image"]:
            image_path = download_image(art["image"])
        
        msg = f"*{title}*\n\n{summary}"
        
        try:
            if image_path and os.path.exists(image_path):
                with open(image_path, 'rb') as photo:
                    files = {'photo': photo}
                    data = {
                        'chat_id': TG_CHAT_ID,
                        'caption': msg,
                        'parse_mode': 'Markdown'
                    }
                    requests.post(
                        f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                        files=files,
                        data=data,
                        timeout=10
                    )
                try:
                    os.remove(image_path)
                except:
                    pass
            else:
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={
                        "chat_id": TG_CHAT_ID,
                        "text": msg,
                        "parse_mode": "Markdown"
                    },
                    timeout=10
                )
            
            safe_log(f"✓ [{i}] {title[:40]}...")
            mark_sent(art["url"], art["title"], summary)
            sent += 1
            
            if i < len(articles):
                time.sleep(2)
        
        except Exception as e:
            safe_log(f"✗ [{i}] {str(e)[:50]}")
    
    return sent

def main():
    safe_log("🚀 LENTA → TELEGRAM")
    
    if not all([HF_TOKEN, TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ ОШИБКА: нет секретов!")
        return
    
    init_db()
    articles = fetch_lenta()
    
    if not articles:
        safe_log("ℹ️ НЕТ НОВОСТЕЙ")
        return
    
    sent = send_to_telegram(articles[:MAX_TOP_ARTICLES])
    safe_log(f"\n✨ ГОТОВО! Отправлено: {sent}")

if __name__ == "__main__":
    main()
