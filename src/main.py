#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import feedparser
from datetime import datetime
import time
import os
import sqlite3
import re
import json

# ============= КОНФИГ =============
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# НАСТРОЙКИ
RSS_URL = "https://lenta.ru/rss"
MAX_TOP_ARTICLES = 5
INTERVAL_BETWEEN_POSTS = 300
DB = "data/sent_links.db"

# ============= LOGGING =============

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
    safe_log("✓ База данных инициализирована")

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

def get_sent_count():
    conn = sqlite3.connect(DB)
    result = conn.execute("SELECT COUNT(*) FROM sent").fetchone()
    conn.close()
    return result[0] if result else 0

# ============= СБОР НОВОСТЕЙ =============

def fetch_all_lenta_rss():
    safe_log(f"📰 Загрузка ВСЕ новости из RSS: {RSS_URL}")
    
    try:
        feed = feedparser.parse(RSS_URL)
        articles = []
        
        for entry in feed.entries[:100]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "")[:300].strip()
            
            # Очищаем от цифр
            title = re.sub(r'\d+$', '', title).strip()
            summary = re.sub(r'\d+$', '', summary).strip()
            
            image = None
            if hasattr(entry, 'media_content') and entry.media_content:
                image = entry.media_content[0].get('url')
            elif hasattr(entry, 'enclosures') and entry.enclosures:
                image = entry.enclosures[0].get('href')
            
            if not title or not link:
                continue
            
            if was_sent(link):
                continue
            
            articles.append({
                "title": title,
                "description": summary,
                "url": link,
                "image": image,
                "source": "Lenta.ru"
            })
        
        safe_log(f"✓ Найдено НОВЫХ новостей: {len(articles)}")
        return articles
        
    except Exception as e:
        safe_log(f"✗ Ошибка загрузки RSS: {e}")
        return []

# ============= БЫСТРАЯ ОЦЕНКА (БЕЗ AI!) =============

def quick_rank_articles(articles):
    """
    Оценивает новости БЕЗ AI - только по ключевым словам!
    100% БЕСПЛАТНО!
    """
    if not articles:
        return []
    
    safe_log(f"🚀 Быстрая оценка {len(articles)} новостей (БЕЗ API)...\n")
    
    # Ключевые слова для определения важности
    critical_words = ['взрыв', 'крах', 'терор', 'война', 'чп', 'катастроф', 'авария', 'смерт', 'убит', 
                      'убийство', 'теракт', 'армия', 'войска', 'бомб', 'удар', 'атак', 'конфликт',
                      'восстани', 'переворот', 'санкци', 'отставк', 'арест', 'скандал']
    
    important_words = ['курс', 'доллар', 'евро', 'криптовалют', 'акци', 'биржа', 'инвестиц',
                       'экономик', 'производ', 'компани', 'корпораци', 'работ', 'безработ',
                       'правител', 'президент', 'министр', 'закон', 'суд', 'технолог',
                       'ai', 'искусствен', 'интернет', 'киберат', 'хакер']
    
    interesting_words = ['кино', 'фильм', 'актер', 'актриса', 'мьюзик', 'певец', 'певиц',
                         'спорт', 'футбол', 'хоккей', 'теннис', 'олимпи', 'чемпион',
                         'конкурс', 'мода', 'красот', 'здоровье', 'медицин', 'наук']
    
    for article in articles:
        title = article.get("title", "").lower()
        desc = article.get("description", "").lower()
        text = title + " " + desc
        
        # Подсчитываем совпадения
        critical_count = sum(1 for word in critical_words if word in text)
        important_count = sum(1 for word in important_words if word in text)
        interesting_count = sum(1 for word in interesting_words if word in text)
        
        # Вычисляем рейтинг
        if critical_count > 0:
            rating = 8 + critical_count  # 8-10+
        elif important_count > 0:
            rating = 6 + min(important_count, 2)  # 6-8
        elif interesting_count > 0:
            rating = 5  # 5
        else:
            rating = 3  # 3
        
        # Ограничиваем от 1 до 10
        rating = min(max(rating, 1), 10)
        
        article["rating"] = rating
        safe_log(f"  ⭐{rating}/10 - {article['title'][:50]}...")
    
    # Сортируем по рейтингу
    articles.sort(key=lambda x: x.get("rating", 0), reverse=True)
    
    # Берём только топ-5
    top_articles = articles[:MAX_TOP_ARTICLES]
    
    safe_log(f"\n✓ Выбраны ТОП-{len(top_articles)} по важности:")
    for idx, art in enumerate(top_articles, 1):
        safe_log(f"   {idx}. ⭐{art['rating']}/10 - {art['title'][:50]}...")
    
    return top_articles

# ============= УЛУЧШАЕМ ТЕКСТ =============

def improve_summary(article):
    """Берём первые 2 предложения из описания - вот и суммаризация!"""
    desc = article.get("description", "")
    
    # Берём первое предложение
    sentences = desc.split('.')
    summary = sentences[0].strip() + "."
    
    if len(sentences) > 1:
        summary += " " + sentences[1].strip() + "."
    
    # Добавляем эмодзи в зависимости от рейтинга
    rating = article.get("rating", 5)
    if rating >= 8:
        emoji = "🔴"  # Критично
    elif rating >= 6:
        emoji = "🟠"  # Важно
    else:
        emoji = "🔵"  # Интересно
    
    summary = emoji + " " + summary[:200]
    summary = re.sub(r'\d+$', '', summary).strip()
    
    return summary

# ============= ОТПРАВКА В TELEGRAM =============

def send_to_telegram(articles):
    if not articles:
        safe_log("⚠️ ❌ НЕТ ДОСТАТОЧНО ХОРОШИХ НОВОСТЕЙ - НИЧЕГО НЕ ПОСТИМ")
        return 0, 0
    
    log_section(f"📤 ОТПРАВКА {len(articles)} ТОПОВЫХ НОВОСТЕЙ")
    
    sent = 0
    failed = 0
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "")
        url = article.get("url", "")
        image = article.get("image", "")
        rating = article.get("rating", 0)
        
        # Улучшаем описание
        summary = improve_summary(article)
        
        # Формируем сообщение
        stars = "⭐" * (rating // 2)
        message = f"""*{title}*

{summary}

{stars}"""
        
        try:
            if image:
                response = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                    json={
                        "chat_id": TG_CHAT_ID,
                        "photo": image,
                        "caption": message,
                        "parse_mode": "Markdown"
                    },
                    timeout=10
                )
            else:
                response = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={
                        "chat_id": TG_CHAT_ID,
                        "text": message,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True
                    },
                    timeout=10
                )
            
            if response.status_code == 200:
                safe_log(f"[{i}/{len(articles)}] ✓ Отправлено: {title[:45]}...")
                mark_sent(url, title)
                sent += 1
            else:
                safe_log(f"[{i}/{len(articles)}] ✗ HTTP {response.status_code}")
                failed += 1
        
        except Exception as e:
            safe_log(f"[{i}/{len(articles)}] ✗ Ошибка: {e}")
            failed += 1
        
        if i < len(articles):
            safe_log(f"⏳ Ожидание 5 минут...")
            time.sleep(INTERVAL_BETWEEN_POSTS)
    
    return sent, failed

# ============= ГЛАВНАЯ =============

def main():
    log_section("🚀 LENTA.RU TOP-5 → TELEGRAM (100% БЕСПЛАТНО!)")
    
    if not all([TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ ОШИБКА: TG_TOKEN или TG_CHAT_ID не установлены!")
        return
    
    safe_log("✓ Telegram ключи загружены")
    safe_log(f"⚙️ Режим: БЕСПЛАТНЫЙ (без AI API!)")
    safe_log(f"💰 ЦЕНА: $0 в месяц!")
    
    init_db()
    
    total_sent = get_sent_count()
    safe_log(f"📊 Всего отправлено: {total_sent} новостей")
    
    log_section("ЭТАП 1: СБОР ВСЕ НОВЫЕ НОВОСТИ")
    articles = fetch_all_lenta_rss()
    
    if not articles:
        safe_log("ℹ️ НОВЫХ НОВОСТЕЙ НЕТ (все уже отправлены)")
        return
    
    log_section("ЭТАП 2: БЫСТРАЯ ОЦЕНКА (БЕЗ AI)")
    top_articles = quick_rank_articles(articles)
    
    log_section("ЭТАП 3: ОТПРАВКА")
    sent, failed = send_to_telegram(top_articles)
    
    log_section("✨ ГОТОВО")
    safe_log(f"✅ Успешно отправлено: {sent} топовых новостей")
    if failed > 0:
        safe_log(f"❌ Ошибок: {failed}")
    
    new_total = get_sent_count()
    safe_log(f"📊 Всего в базе: {new_total} новостей")
    safe_log(f"\n💰 ЗАТРАТЫ: $0.00 (БЕЗ API!)")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        safe_log(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
