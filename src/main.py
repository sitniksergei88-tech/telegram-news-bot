#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import feedparser
from datetime import datetime
from openai import OpenAI
import time
import os
import sqlite3

# ============= КОНФИГ =============
PERPLEXITY_KEY = os.getenv("PERPLEXITY_KEY")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# НАСТРОЙКИ
RSS_URL = "https://lenta.ru/rss"
MAX_TOP_ARTICLES = 5  # МАКСИМУМ 5 топовых в час
INTERVAL_BETWEEN_POSTS = 300  # 5 минут между постами
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

# ============= БД (ДЕДУПЛИКАЦИЯ) =============

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
    """Проверяет, отправлялась ли новость раньше"""
    conn = sqlite3.connect(DB)
    result = conn.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone()
    conn.close()
    return result is not None

def mark_sent(url, title):
    """Сохраняет новость как отправленную"""
    conn = sqlite3.connect(DB)
    conn.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, ?)", 
                 (url, title, datetime.now()))
    conn.commit()
    conn.close()

def get_sent_count():
    """Возвращает количество отправленных новостей"""
    conn = sqlite3.connect(DB)
    result = conn.execute("SELECT COUNT(*) FROM sent").fetchone()
    conn.close()
    return result[0] if result else 0

# ============= СБОР ВСЕ НОВОСТЕЙ ИЗ LENTA.RU =============

def fetch_all_lenta_rss():
    """
    Собирает ВСЕ новые новости из Lenta.ru (за час)
    Пропускает только уже отправленные
    """
    safe_log(f"📰 Загрузка ВСЕ новости из RSS: {RSS_URL}")
    
    try:
        feed = feedparser.parse(RSS_URL)
        articles = []
        
        for entry in feed.entries[:100]:  # Ищем в 100 последних
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "")[:300].strip()
            
            # Извлекаем фото из RSS
            image = None
            if hasattr(entry, 'media_content') and entry.media_content:
                image = entry.media_content[0].get('url')
            elif hasattr(entry, 'enclosures') and entry.enclosures:
                image = entry.enclosures[0].get('href')
            
            if not title or not link:
                continue
            
            # ВАЖНО: Пропускаем ЕСЛИ УЖЕ ОТПРАВЛЯЛИ
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

# ============= AI ОЦЕНКА КАЧЕСТВА И РАНЖИРОВАНИЕ =============

def rank_and_summarize_with_perplexity(articles):
    """
    1. Оценивает КАЧЕСТВО каждой новости (1-10)
    2. Сортирует по качеству (топовые первыми)
    3. Берёт только ТОП-5 лучших
    4. Суммаризирует их
    """
    if not articles:
        return []
    
    safe_log(f"🤖 Perplexity: ранжирование + суммаризация {len(articles)} новостей...\n")
    
    client = create_perplexity_client()
    rated_articles = []
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "")
        desc = article.get("description", "")
        
        # ЭТАП 1: Оцениваем ВАЖНОСТЬ/КАЧЕСТВО новости
        rating_prompt = f"""Оцени ВАЖНОСТЬ этой новости (от 1 до 10):

Заголовок: {title}
Текст: {desc}

Критерии:
- 9-10: ОЧЕНЬ ВАЖНАЯ (критические события, ЧП, политика)
- 7-8: ВАЖНАЯ (значимые события, бизнес, технологии)
- 5-6: ИНТЕРЕСНАЯ (культура, общество, спорт)
- 1-4: МАЛОВАЖНАЯ (развлечение, мелочи)

ОТВЕТЬ ТОЛЬКО ЧИСЛОМ (1-10)!"""
        
        try:
            rating_response = client.chat.completions.create(
                model="sonar",
                messages=[{"role": "user", "content": rating_prompt}],
                max_tokens=5,
                temperature=0.3
            )
            
            rating_text = rating_response.choices[0].message.content.strip()
            rating = int(''.join(filter(str.isdigit, rating_text)) or 0)
            
            safe_log(f"  [{i}] Оценка: {rating}/10 - {title[:50]}...")
            
            # ЭТАП 2: Суммаризируем
            summary_prompt = f"""Напиши краткую, интересную сводку для Telegram (2-3 предложения):

Заголовок: {title}
Текст: {desc}

Требования:
- 2-3 предложения (не больше!)
- Добавь 1-2 эмодзи
- Сделай интересным
- Не повторяй заголовок"""
            
            summary_response = client.chat.completions.create(
                model="sonar",
                messages=[{"role": "user", "content": summary_prompt}],
                max_tokens=150,
                temperature=0.7
            )
            
            article["summary"] = summary_response.choices[0].message.content.strip()
            article["rating"] = rating
            rated_articles.append(article)
            safe_log(f"      ✓ ОЦЕНЕНА")
            
        except Exception as e:
            safe_log(f"  [{i}] ✗ Ошибка: {e}")
            continue
        
        time.sleep(0.3)
    
    # СОРТИРУЕМ ПО РЕЙТИНГУ (больше = лучше)
    safe_log(f"\n📊 Сортировка по качеству...")
    rated_articles.sort(key=lambda x: x.get("rating", 0), reverse=True)
    
    # БЕРЁМ ТОЛЬКО ТОП-5
    top_articles = rated_articles[:MAX_TOP_ARTICLES]
    
    safe_log(f"✓ Выбраны ТОП-{len(top_articles)} лучших новостей:")
    for idx, art in enumerate(top_articles, 1):
        safe_log(f"   {idx}. ⭐{art['rating']}/10 - {art['title'][:50]}...")
    
    return top_articles

# ============= ОТПРАВКА В TELEGRAM =============

def send_to_telegram(articles):
    """Отправляет ТОП новости в Telegram с интервалом 5 минут"""
    if not articles:
        safe_log("⚠️ ❌ НЕТ ДОСТАТОЧНО ХОРОШИХ НОВОСТЕЙ - НИЧЕГО НЕ ПОСТИМ")
        return 0, 0
    
    log_section(f"📤 ОТПРАВКА {len(articles)} ТОПОВЫХ НОВОСТЕЙ")
    
    sent = 0
    failed = 0
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "")
        summary = article.get("summary", "")
        url = article.get("url", "")
        image = article.get("image", "")
        rating = article.get("rating", 0)
        
        # Формируем сообщение с рейтингом (звёзды)
        stars = "⭐" * (rating // 2)  # Переводим в звёзды: 10→5⭐, 8→4⭐
        message = f"""*{title}*

{summary}

{stars}"""
        
        try:
            # Если есть фото → отправляем с фото
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
                # Если нет фото → отправляем текст
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
        
        # Ждем 5 минут перед следующим постом (кроме последнего)
        if i < len(articles):
            safe_log(f"⏳ Ожидание 5 минут...")
            time.sleep(INTERVAL_BETWEEN_POSTS)
    
    return sent, failed

# ============= ГЛАВНАЯ =============

def main():
    log_section("🚀 LENTA.RU TOP-5 → TELEGRAM")
    
    # Проверяем ключи
    if not all([PERPLEXITY_KEY, TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ ОШИБКА: Не все ключи установлены!")
        return
    
    safe_log("✓ Все ключи загружены")
    safe_log(f"⚙️ Режим: ВСЕ новости за час → выбираем ТОП-{MAX_TOP_ARTICLES}")
    safe_log(f"⚙️ Интервал: {INTERVAL_BETWEEN_POSTS//60} минут между постами")
    
    # Инициализируем БД
    init_db()
    
    # Статистика
    total_sent = get_sent_count()
    safe_log(f"📊 Всего отправлено: {total_sent} новостей")
    
    # ЭТАП 1: Сбор ВСЕ новых новостей (за час)
    log_section("ЭТАП 1: СБОР ВСЕ НОВЫЕ НОВОСТИ")
    articles = fetch_all_lenta_rss()
    
    if not articles:
        safe_log("ℹ️ НОВЫХ НОВОСТЕЙ НЕТ (все уже отправлены)")
        return
    
    # ЭТАП 2: Ранжирование + выбор топ-5 + суммаризация
    log_section("ЭТАП 2: РАНЖИРОВАНИЕ И ВЫБОР ТОП-5")
    top_articles = rank_and_summarize_with_perplexity(articles)
    
    # ЭТАП 3: Отправка (или нет, если плохих новостей)
    log_section("ЭТАП 3: ОТПРАВКА")
    sent, failed = send_to_telegram(top_articles)
    
    # Финал
    log_section("✨ ГОТОВО")
    safe_log(f"✅ Успешно отправлено: {sent} топовых новостей")
    if failed > 0:
        safe_log(f"❌ Ошибок: {failed}")
    
    new_total = get_sent_count()
    safe_log(f"📊 Всего в базе: {new_total} новостей")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        safe_log(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
