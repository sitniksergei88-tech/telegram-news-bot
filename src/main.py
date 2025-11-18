#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import requests
import feedparser
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# ============= НАСТРОЙКИ =============
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
RSS_URL = "https://lenta.ru/rss/news/world"

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "sent_links.db"

DELAY_BETWEEN_POSTS = 3  # Пауза между постами (секунды)

# ============= ИНИЦИАЛИЗАЦИЯ =============
DATA_DIR.mkdir(exist_ok=True)

def init_db():
    """Инициализация БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent_links (
            link TEXT PRIMARY KEY,
            title TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def is_link_sent(link):
    """Проверить, отправлена ли ссылка"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM sent_links WHERE link = ?", (link,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def add_sent_link(link, title):
    """Добавить ссылку в БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO sent_links (link, title) VALUES (?, ?)", (link, title))
    conn.commit()
    conn.close()

def fetch_rss():
    """Загрузить RSS с DEBUG инфо"""
    print("=" * 60)
    print("🔍 ТЕСТИРОВАНИЕ ДОСТУПА К RSS")
    print("=" * 60)
    
    try:
        print(f"📡 Запрос: {RSS_URL}")
        response = requests.get(RSS_URL, timeout=15)
        print(f"✅ Статус: {response.status_code}")
        print(f"📊 Размер ответа: {len(response.content)} байт")
        print("=" * 60)
        
        if response.status_code != 200:
            print(f"❌ ОШИБКА: Статус {response.status_code}")
            return None
        
        if len(response.content) == 0:
            print("❌ ОШИБКА: Пустой ответ (0 байт)")
            return None
        
        # Сохранить для дебага
        debug_file = DATA_DIR / "rss_debug.xml"
        with open(debug_file, "wb") as f:
            f.write(response.content)
        print(f"💾 RSS сохранен в: {debug_file}")
        
        return feedparser.parse(response.content)
    
    except requests.exceptions.Timeout:
        print("❌ ОШИБКА: Timeout (15 сек)")
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"❌ ОШИБКА: Нет соединения - {e}")
        return None
    except Exception as e:
        print(f"❌ ОШИБКА: {type(e).__name__} - {e}")
        return None

def send_to_telegram(title, link, image_url=None):
    """Отправить сообщение в Telegram"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    
    text = f"📰 <b>{title}</b>\n\n🔗 <a href='{link}'>Читать полностью</a>"
    
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"✅ Отправлено: {title[:50]}...")
            return True
        else:
            print(f"❌ Ошибка Telegram: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return False

def main():
    print("\n🤖 LENTA WORLD → TELEGRAM BOT")
    print(f"⏰ Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Проверить переменные окружения
    if not TG_TOKEN or not TG_CHAT_ID:
        print("❌ ОШИБКА: Не установлены TG_TOKEN или TG_CHAT_ID")
        sys.exit(1)
    
    # Инициализация
    init_db()
    
    # Загрузить RSS с дебагом
    feed = fetch_rss()
    
    if not feed or not feed.entries:
        print("❌ НЕТ НОВОСТЕЙ В RSS ИЛИ ОШИБКА ЗАГРУЗКИ")
        print("=" * 60)
        sys.exit(1)
    
    print(f"\n📝 Найдено записей в RSS: {len(feed.entries)}")
    
    # Обработать новости
    new_count = 0
    sent_count = 0
    
    for entry in feed.entries:
        link = entry.get("link", "")
        title = entry.get("title", "Без заголовка")
        
        if not link:
            continue
        
        # Проверить, отправлена ли
        if is_link_sent(link):
            print(f"⏭️  Уже отправлена: {title[:40]}...")
            continue
        
        new_count += 1
        print(f"\n🆕 Новость #{new_count}: {title[:50]}...")
        
        # Отправить в Telegram
        if send_to_telegram(title, link):
            add_sent_link(link, title)
            sent_count += 1
            
            # Пауза между постами
            if new_count < len(feed.entries):
                import time
                time.sleep(DELAY_BETWEEN_POSTS)
    
    print("\n" + "=" * 60)
    print(f"📊 РЕЗУЛЬТАТЫ:")
    print(f"   ✅ Отправлено: {sent_count}")
    print(f"   ⏭️  Пропущено (уже отправлены): {len(feed.entries) - new_count}")
    print(f"   🆕 Всего новых: {new_count}")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
