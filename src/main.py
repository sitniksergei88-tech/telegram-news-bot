#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import feedparser
import time
import os
import sqlite3
import re
from datetime import datetime, timedelta
import random

HF_TOKEN = os.getenv("HF_API_TOKEN")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

RSS_URL = "https://lenta.ru/rss"
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

def parse_rss_time(time_str):
    """Парсит время из RSS (RFC 2822)"""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(time_str)
        return dt.replace(tzinfo=None)
    except:
        return None

def is_within_last_hour(article_time):
    """Проверяет, была ли новость опубликована за последний час"""
    if not article_time:
        return True
    
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    
    return one_hour_ago <= article_time <= now

def fetch_lenta_last_hour():
    """Загружает ТОЛЬКО новости за последний час"""
    safe_log("📰 Загрузка новостей за последний час...")
    feed = feedparser.parse(RSS_URL)
    articles = []
    
    for entry in feed.entries[:100]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        desc = entry.get("summary", "")[:400].strip()
        
        # Парсим время публикации
        published = entry.get("published", "")
        article_time = parse_rss_time(published)
        
        # Проверяем, что новость за последний час
        if not is_within_last_hour(article_time):
            continue
        
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
            "image": image_url,
            "time": article_time
        })
    
    safe_log(f"✓ Найдено за последний час: {len(articles)}")
    return articles

def rank_articles_with_ai(articles):
    """ИИ ранжирует новости и выбирает топ 1-5"""
    if not articles or not HF_TOKEN:
        return articles[:5]
    
    if len(articles) <= 5:
        return articles
    
    safe_log(f"🤖 ИИ ранжирует {len(articles)} новостей...")
    
    # Формируем список новостей для ИИ
    articles_text = "\n".join([f"{i+1}. [{a['title']}] {a['desc'][:100]}" for i, a in enumerate(articles[:20])])
    
    prompt = f"""Ты редактор новостного канала. Выбери самые ВАЖНЫЕ и ИНТЕРЕСНЫЕ новости из этого списка.

Критерии:
- Большое влияние на общество
- Интересна для широкой аудитории
- Актуальна и важна
- НЕ повторяющаяся информация

Список новостей:
{articles_text}

Выбери номера топ 3-5 самых лучших новостей ЧЕРЕЗ ЗАПЯТУЮ (например: 1,3,5,7).
Ответ - ТОЛЬКО НОМЕРА!"""
    
    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 50,
                    "temperature": 0.5,
                    "do_sample": False
                }
            },
            timeout=25
        )
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                result = data[0].get("generated_text", "").strip()
                
                # Извлекаем номера
                numbers_str = result.split('\n')[-1].strip()
                numbers = [int(n.strip())-1 for n in numbers_str.split(',') if n.strip().isdigit()]
                numbers = [n for n in numbers if 0 <= n < len(articles)]
                
                if numbers:
                    selected = [articles[i] for i in numbers]
                    safe_log(f"✓ ИИ выбрал новости: {[i+1 for i in numbers]}")
                    return selected
    except Exception as e:
        safe_log(f"⚠️ Ошибка ранжирования: {str(e)[:40]}")
    
    # Fallback: если ИИ не сработал, берем первые 5
    return articles[:5]

def rewrite_with_hf(title, text):
    """Переписывает текст с HuggingFace Qwen2.5-7B"""
    if not HF_TOKEN:
        return text[:150]
    
    prompt = f"""Переписи эту новость в 2-3 коротких предложениях на русском. Не копируй исходный текст, сделай свою версию!

Заголовок: {title}
Текст: {text}

Ответ (только переписанный текст, без пояснений):"""
    
    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 80,
                    "temperature": 0.7,
                    "do_sample": True,
                    "top_p": 0.9
                }
            },
            timeout=25
        )
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                result = data[0].get("generated_text", "").strip()
                if prompt in result:
                    result = result.split(prompt)[-1].strip()
                sentences = result.split('.')[:2]
                result = '.'.join(s.strip() for s in sentences if s.strip()) + '.'
                result = re.sub(r'\d+$', '', result).strip()
                return result[:200] if len(result) > 15 else text[:150]
    except Exception as e:
        safe_log(f"⚠️ HF ошибка: {str(e)[:40]}")
    
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
    """Отправляет новости с интервалом 5-10 минут"""
    if not articles:
        safe_log("⚠️ НЕТ НОВОСТЕЙ")
        return 0
    
    safe_log(f"📤 Публикую {len(articles)} лучших новостей с интервалом...\n")
    sent = 0
    
    for i, art in enumerate(articles):
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
            
            safe_log(f"✓ [{i+1}] {title[:40]}...")
            mark_sent(art["url"], art["title"], summary)
            sent += 1
            
            # Интервал между постами 5-10 минут (но в GitHub Actions делаем меньше)
            if i < len(articles) - 1:
                # В тесте 10 сек, в продакшене раскомментить на 300-600
                time.sleep(10)
        
        except Exception as e:
            safe_log(f"✗ [{i+1}] {str(e)[:50]}")
    
    return sent

def main():
    safe_log("🚀 LENTA → TELEGRAM (SMART RANKING)")
    safe_log(f"⏰ Анализ новостей за последний час...\n")
    
    if not all([HF_TOKEN, TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ ОШИБКА: нет секретов!")
        return
    
    init_db()
    
    # Загружаем новости за последний час
    articles = fetch_lenta_last_hour()
    
    if not articles:
        safe_log("ℹ️ НЕТ НОВОСТЕЙ ЗА ПОСЛЕДНИЙ ЧАС")
        return
    
    # ИИ выбирает топ 1-5
    top_articles = rank_articles_with_ai(articles)
    
    # Отправляем с интервалом
    sent = send_to_telegram(top_articles)
    safe_log(f"\n✨ ГОТОВО! Опубликовано: {sent}/{len(top_articles)}")

if __name__ == "__main__":
    main()
