#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import feedparser
from datetime import datetime
import time
import os
import sqlite3
import re

# ============= КОНФИГ =============
HF_TOKEN = os.getenv("HF_API_TOKEN")  # Бесплатный HuggingFace API
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# НАСТРОЙКИ
RSS_URL = "https://lenta.ru/rss"
MAX_TOP_ARTICLES = 5
INTERVAL_BETWEEN_POSTS = 300
DB = "data/sent_links.db"

# ============= HUGGINGFACE INFERENCE API (БЕСПЛАТНЫЙ!) =============

def call_hf_model(prompt):
    """
    HuggingFace Inference API - БЕСПЛАТНЫЙ!
    https://huggingface.co/settings/tokens
    
    ✅ 250,000 символов текста БЕСПЛАТНО в месяц
    ✅ Работает из России
    ✅ Готовые открытые модели (Mistral, Llama2)
    ✅ На GitHub Actions работает
    """
    try:
        # Используем Mistral-7B (хорошее соотношение качество/скорость)
        response = requests.post(
            "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 150,
                    "temperature": 0.7,
                    "do_sample": True
                }
            },
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                result = data[0].get("generated_text", "").strip()
                # Удаляем исходный промпт из результата
                if prompt in result:
                    result = result.replace(prompt, "").strip()
                result = re.sub(r'\d+$', '', result).strip()
                return result[:200]
        else:
            safe_log(f"  ⚠️ HF ошибка {response.status_code}")
            return None
    except Exception as e:
        safe_log(f"  ✗ HF: {e}")
        return None

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
            summary TEXT,
            time TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    safe_log("✓ База данных готова")

def was_sent(url):
    conn = sqlite3.connect(DB)
    result = conn.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone()
    conn.close()
    return result is not None

def mark_sent(url, title, summary):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, ?, ?)", 
                 (url, title, summary, datetime.now()))
    conn.commit()
    conn.close()

def get_sent_count():
    conn = sqlite3.connect(DB)
    result = conn.execute("SELECT COUNT(*) FROM sent").fetchone()
    conn.close()
    return result[0] if result else 0

# ============= СБОР НОВОСТЕЙ =============

def fetch_all_lenta_rss():
    safe_log(f"📰 Lenta.ru RSS...")
    
    try:
        feed = feedparser.parse(RSS_URL)
        articles = []
        
        for entry in feed.entries[:100]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "")[:400].strip()
            
            title = re.sub(r'\d+$', '', title).strip()
            summary = re.sub(r'\d+$', '', summary).strip()
            
            image = None
            if hasattr(entry, 'media_content') and entry.media_content:
                image = entry.media_content[0].get('url')
            elif hasattr(entry, 'enclosures') and entry.enclosures:
                image = entry.enclosures[0].get('href')
            
            if not title or not link or len(summary) < 20:
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
        
        safe_log(f"✓ Найдено: {len(articles)}")
        return articles
        
    except Exception as e:
        safe_log(f"✗ Ошибка: {e}")
        return []

# ============= ОЦЕНКА + ПЕРЕПИСАНИЕ =============

def rank_and_rewrite(articles):
    """
    Оценивает + переписывает текст с помощью открытой модели
    """
    if not articles:
        return []
    
    safe_log(f"🤖 HuggingFace (Mistral): оценка + переписание {len(articles)}...\n")
    
    rated_articles = []
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "")
        desc = article.get("description", "")
        
        # Более простой промпт для открытой модели
        prompt = f"""Оцени новость от 1 до 10 и переписи в 2 предложениях.

Заголовок: {title}
Текст: {desc}

Ответ:
Оценка: [число]
Текст: [2 предложения]"""
        
        try:
            response_text = call_hf_model(prompt)
            
            if not response_text:
                safe_log(f"  [{i}] ⚠️ Модель не ответила")
                continue
            
            # Парсим ответ
            rating = 5
            new_summary = desc[:200]
            
            # Ищем оценку
            lines = response_text.split('\n')
            for line in lines:
                if 'оценка' in line.lower() or 'Оценка' in line:
                    try:
                        rating = int(''.join(filter(str.isdigit, line[:20])) or 5)
                        rating = min(max(rating, 1), 10)
                    except:
                        pass
                if 'текст' in line.lower() or 'Текст' in line:
                    idx = lines.index(line)
                    new_summary = '\n'.join(lines[idx:])
                    new_summary = new_summary.replace('текст:', '').replace('Текст:', '').strip()
            
            # Если парсинг не сработал - берём весь результат
            if not new_summary or len(new_summary) < 10:
                new_summary = response_text
            
            new_summary = new_summary[:200].strip()
            
            # Убираем цифры в конце
            new_summary = re.sub(r'\d+$', '', new_summary).strip()
            
            if new_summary and len(new_summary) > 10:
                article["summary"] = new_summary
                article["rating"] = rating
                rated_articles.append(article)
                
                safe_log(f"  [{i}] ⭐{rating}/10 - {title[:35]}...")
            else:
                safe_log(f"  [{i}] ⚠️ Плохой результат")
            
        except Exception as e:
            safe_log(f"  [{i}] ✗ Ошибка: {e}")
            continue
        
        time.sleep(1)  # Щадим API
    
    # Сортируем
    if not rated_articles:
        safe_log("⚠️ Нет обработанных новостей")
        return []
    
    safe_log(f"\n📊 Сортировка...")
    rated_articles.sort(key=lambda x: x.get("rating", 0), reverse=True)
    
    top_articles = rated_articles[:MAX_TOP_ARTICLES]
    
    safe_log(f"✓ ТОП-{len(top_articles)}:")
    for idx, art in enumerate(top_articles, 1):
        safe_log(f"   {idx}. ⭐{art['rating']}/10 - {art['title'][:35]}...")
    
    return top_articles

# ============= ОТПРАВКА В TELEGRAM =============

def send_to_telegram(articles):
    if not articles:
        safe_log("⚠️ НЕТ НОВОСТЕЙ")
        return 0, 0
    
    log_section(f"📤 ОТПРАВКА {len(articles)}")
    
    sent = 0
    failed = 0
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "")
        summary = article.get("summary", "")
        url = article.get("url", "")
        image = article.get("image", "")
        rating = article.get("rating", 0)
        
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
                safe_log(f"[{i}] ✓ {title[:35]}...")
                mark_sent(url, title, summary)
                sent += 1
            else:
                safe_log(f"[{i}] ✗ HTTP {response.status_code}")
                failed += 1
        
        except Exception as e:
            safe_log(f"[{i}] ✗ {e}")
            failed += 1
        
        if i < len(articles):
            safe_log(f"⏳ 5 минут...")
            time.sleep(INTERVAL_BETWEEN_POSTS)
    
    return sent, failed

# ============= ГЛАВНАЯ =============

def main():
    log_section("🚀 LENTA.RU TOP-5 → TELEGRAM (HuggingFace - OPEN SOURCE!)")
    
    if not all([HF_TOKEN, TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ ОШИБКА: нужны ключи!")
        safe_log("   HF_API_TOKEN (https://huggingface.co/settings/tokens)")
        safe_log("   TG_TOKEN")
        safe_log("   TG_CHAT_ID")
        return
    
    safe_log("✓ Ключи готовы")
    safe_log(f"💰 HuggingFace: БЕСПЛАТНО (250k символов/месяц)")
    safe_log(f"🔓 Модель: Mistral-7B (открытая!)")
    
    init_db()
    
    total_sent = get_sent_count()
    safe_log(f"📊 Всего: {total_sent}")
    
    log_section("ЭТАП 1: СБОР")
    articles = fetch_all_lenta_rss()
    
    if not articles:
        safe_log("ℹ️ НОВОСТЕЙ НЕТ")
        return
    
    log_section("ЭТАП 2: ОЦЕНКА + ПЕРЕПИСАНИЕ (Open Source)")
    top_articles = rank_and_rewrite(articles)
    
    if not top_articles:
        safe_log("⚠️ Не удалось обработать новости")
        return
    
    log_section("ЭТАП 3: ОТПРАВКА")
    sent, failed = send_to_telegram(top_articles)
    
    log_section("✨ ГОТОВО")
    safe_log(f"✅ Отправлено: {sent}")
    new_total = get_sent_count()
    safe_log(f"📊 Всего: {new_total}")
    safe_log(f"\n💰 СТОИМОСТЬ: БЕСПЛАТНО (Open Source!)")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        safe_log(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
