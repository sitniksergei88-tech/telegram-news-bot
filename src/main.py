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

RSS_URL = "https://lenta.ru/rss/news/world"
DB = "data/sent_links.db"

def safe_log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent (
            url TEXT PRIMARY KEY,
            title TEXT,
            summary TEXT,
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

def mark_sent(url, title, summary):
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT OR IGNORE INTO sent VALUES (?, ?, ?, ?)",
        (url, title, summary, time.strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()
    conn.close()

def fetch_all_news():
    """
    Загружает ВСЕ новости из world RSS.
    Берёт только те, что ещё не отправляли.
    """
    safe_log("📰 Загрузка новостей из Lenta World...")
    feed = feedparser.parse(RSS_URL)
    articles = []

    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        desc = (entry.get("summary") or "")[:500].strip()

        if not title or not link or len(desc) < 20:
            continue

        # Пропускаем уже отправленные
        if was_sent(link):
            safe_log(f"  ⏭️  (уже отправлено) {title[:40]}...")
            continue

        # Картинка (если есть)
        image_url = None
        if hasattr(entry, 'media_content') and entry.media_content:
            image_url = entry.media_content[0].get('url')
        if not image_url and hasattr(entry, 'enclosures') and entry.enclosures:
            image_url = entry.enclosures[0].get('href')

        articles.append({
            "title": title,
            "desc": desc,
            "url": link,
            "image": image_url
        })

    safe_log(f"✓ Загружено НОВЫХ новостей: {len(articles)}")
    return articles

def rewrite_with_qwen(title, text):
    """
    Qwen переписывает новость в 2-3 предложения.
    """
    if not HF_TOKEN:
        # Если нет токена - просто обрезаем текст
        return text[:200]

    prompt = f"""Перепиши кратко эту мировую новость в 2-3 предложения на русском.
Сохрани суть, но переделай текст своими словами.

Заголовок: {title}
Текст: {text}

Переписанный текст:"""

    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 80,
                    "temperature": 0.7,
                    "do_sample": True
                }
            },
            timeout=20
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                result = data[0].get("generated_text", "").strip()
                if prompt in result:
                    result = result.split(prompt)[-1].strip()
                
                # Берём только первые 2-3 предложения
                sentences = [s.strip() for s in result.split(".") if s.strip()]
                if sentences:
                    result = ". ".join(sentences[:3]) + "."
                    if len(result) > 20:
                        return result[:300]
    except Exception as e:
        safe_log(f"  ⚠️  Qwen ошибка: {str(e)[:50]}")

    return text[:200]

def download_image(url):
    """Загружает изображение новости."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            os.makedirs("data", exist_ok=True)
            path = os.path.join("data", f"img_{int(time.time())}.jpg")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except:
        pass
    return None

def send_to_telegram(articles):
    """Публикует все новости в Telegram."""
    if not articles:
        safe_log("⚠️ НЕТ НОВЫХ НОВОСТЕЙ")
        return 0

    safe_log(f"📤 Публикую {len(articles)} новостей...\n")
    sent = 0

    for i, art in enumerate(articles, 1):
        title = art["title"]
        # Переписываем текст с Qwen
        summary = rewrite_with_qwen(title, art["desc"])

        msg = f"*{title}*\n\n{summary}"
        image_path = download_image(art.get("image"))

        try:
            # Отправляем с картинкой или без
            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as photo:
                    files = {"photo": photo}
                    data = {
                        "chat_id": TG_CHAT_ID,
                        "caption": msg,
                        "parse_mode": "Markdown"
                    }
                    r = requests.post(
                        f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                        files=files,
                        data=data,
                        timeout=15
                    )
                try:
                    os.remove(image_path)
                except:
                    pass
            else:
                r = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={
                        "chat_id": TG_CHAT_ID,
                        "text": msg,
                        "parse_mode": "Markdown"
                    },
                    timeout=15
                )

            if r.status_code == 200:
                safe_log(f"✓ [{i}] {title[:50]}...")
                mark_sent(art["url"], art["title"], summary)
                sent += 1
            else:
                safe_log(f"✗ [{i}] Ошибка Telegram (код {r.status_code})")

            # Пауза между постами
            if i < len(articles):
                time.sleep(5)

        except Exception as e:
            safe_log(f"✗ [{i}] Ошибка: {str(e)[:60]}")

    return sent

def main():
    safe_log("🚀 LENTA WORLD → TELEGRAM")
    safe_log("=" * 60)

    if not all([TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ ОШИБКА: нет TG_TOKEN или TG_CHAT_ID")
        return

    init_db()
    
    # Загружаем все новые новости
    articles = fetch_all_news()
    
    if not articles:
        safe_log("ℹ️ НЕТ НОВЫХ НОВОСТЕЙ")
        return

    # Публикуем ВСЕ
    sent = send_to_telegram(articles)
    
    safe_log("=" * 60)
    safe_log(f"✨ Опубликовано: {sent} новостей")

if __name__ == "__main__":
    main()
