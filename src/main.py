#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import feedparser
import time
import os
import sqlite3
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

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
        (url, title, summary, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()
    conn.close()

def fetch_lenta_news():
    """
    Загружает ВСЕ новости из RSS за последние часы (без строгого фильтра по часам).
    Берёт свежие новости, которые ещё не отправляли.
    """
    safe_log("📰 Загрузка новостей из Lenta...")
    feed = feedparser.parse(RSS_URL)
    articles = []

    for entry in feed.entries[:100]:  # Берём до 100 последних записей
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        desc = (entry.get("summary") or "")[:400].strip()

        # Чистим от цифр в конце
        title = re.sub(r'\d+$', '', title).strip()
        desc = re.sub(r'\d+$', '', desc).strip()

        # Картинка
        image_url = None
        if hasattr(entry, 'media_content') and entry.media_content:
            image_url = entry.media_content[0].get('url')
        if not image_url and hasattr(entry, 'enclosures') and entry.enclosures:
            image_url = entry.enclosures[0].get('href')

        if not title or not link or len(desc) < 30:
            continue

        # Пропускаем уже отправленные
        if was_sent(link):
            continue

        articles.append({
            "title": title,
            "desc": desc,
            "url": link,
            "image": image_url
        })

    safe_log(f"✓ Загружено свежих новостей: {len(articles)}")
    return articles

def rank_articles_with_qwen(articles):
    """
    Qwen выбирает ТОП 3-5 новостей по важности.
    Это ГЛАВНАЯ функция ранжирования!
    """
    if not articles or not HF_TOKEN:
        safe_log("⚠️ Нет новостей или HF_TOKEN, беру первые 5")
        return articles[:5]

    if len(articles) <= 5:
        safe_log(f"📊 Всего {len(articles)} новостей, все подходят")
        return articles

    safe_log(f"🤖 Qwen ранжирует {len(articles)} новостей, выбирает ТОП 3-5...")

    # Формируем список для Qwen
    items_text = "\n".join(
        f"{i+1}. [{a['title']}] {a['desc'][:150]}"
        for i, a in enumerate(articles[:50])  # Максимум 50 для промпта
    )

    prompt = f"""Ты главный редактор Telegram-канала с серьёзными новостями.
Выбери 3-5 САМЫХ ВАЖНЫХ новостей из этого списка.

КРИТЕРИИ ВАЖНОСТИ (в порядке приоритета):
1. ПОЛИТИКА И ВЛАСТЬ (указы, выборы, смены правительства, санкции)
2. ВОЙНЫ, КОНФЛИКТЫ, ЧП (боевые действия, теракты, катастрофы)
3. ЭКОНОМИКА (крахи банков, девальвация, санкции, инфляция)
4. ГРОМКИЕ РАССЛЕДОВАНИЯ И СКАНДАЛЫ
5. События с массовым влиянием на жизнь людей

ВЫБЕРИ самые важные - то, что читатели обязательно должны знать сегодня!

Список новостей:
{items_text}

Ответь ТОЛЬКО НОМЕРАМИ через запятую, без объяснений! (например: 2,5,8,12)"""

    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 30,
                    "temperature": 0.2,  # Пониже температура - более консервативные выборы
                    "do_sample": False
                }
            },
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                text = data[0].get("generated_text", "").strip()
                # Берём последнюю строку как ответ
                lines = text.split("\n")
                answer_line = lines[-1] if lines else ""
                
                safe_log(f"🤖 Qwen ответил: {answer_line}")
                
                # Парсим номера
                nums = []
                for part in answer_line.replace(" ", "").split(","):
                    if part.isdigit():
                        idx = int(part) - 1
                        if 0 <= idx < len(articles):
                            nums.append(idx)
                
                # Убираем дубликаты и сохраняем порядок
                nums = list(dict.fromkeys(nums))
                
                if nums and len(nums) >= 1:
                    chosen = [articles[i] for i in nums]
                    safe_log(f"✓ Выбрано новостей: {len(chosen)} (номера: {[i+1 for i in nums]})")
                    return chosen
                else:
                    safe_log("⚠️ Qwen не вернул валидные номера, беру первые 5")
                    return articles[:5]

    except Exception as e:
        safe_log(f"⚠️ Ошибка Qwen ранжирования: {str(e)[:80]}")

    # Fallback - если что-то пошло не так
    safe_log("📊 Fallback: беру первые 5 новостей")
    return articles[:5]

def rewrite_with_qwen(title, text):
    """
    Qwen переписывает новость в 2–3 предложения живым языком.
    """
    if not HF_TOKEN:
        return text[:180]

    prompt = f"""Перепиши эту новость в 2–3 коротких живых предложения на русском.
ВАЖНО: Не копируй исходный текст! Переделай своими словами, добавь контекст.

Заголовок: {title}
Текст: {text}

Переписанный текст (только он, без объяснений):"""

    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 100,
                    "temperature": 0.8,
                    "top_p": 0.95,
                    "do_sample": True
                }
            },
            timeout=25
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                result = data[0].get("generated_text", "").strip()
                if prompt in result:
                    result = result.split(prompt)[-1].strip()
                
                # Берём 2-3 предложения
                sentences = [s.strip() for s in result.split(".") if s.strip()]
                result = ". ".join(sentences[:3]) + "."
                result = re.sub(r'\d+$', '', result).strip()
                
                if len(result) > 30:
                    return result[:400]
    except Exception as e:
        safe_log(f"⚠️ Ошибка переписи: {str(e)[:60]}")

    return text[:180]

def download_image(url):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=7)
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
    """
    Публикует новости в Telegram с интервалами.
    """
    if not articles:
        safe_log("⚠️ НЕТ НОВОСТЕЙ ДЛЯ ПУБЛИКАЦИИ")
        return 0

    safe_log(f"📤 Отправляю {len(articles)} новостей в Telegram...\n")
    sent = 0

    for i, art in enumerate(articles, 1):
        title = art["title"]
        summary = rewrite_with_qwen(title, art["desc"])

        msg = f"*{title}*\n\n{summary}"
        image_path = download_image(art.get("image"))

        try:
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
                safe_log(f"✓ [{i}] Отправлено: {title[:50]}...")
                mark_sent(art["url"], art["title"], summary)
                sent += 1
            else:
                safe_log(f"✗ [{i}] Ошибка отправки (код {r.status_code})")

            # Интервал между постами (5-10 сек для тестов, можно увеличить)
            if i < len(articles):
                time.sleep(8)

        except Exception as e:
            safe_log(f"✗ [{i}] Ошибка: {str(e)[:80]}")

    return sent

def main():
    safe_log("🚀 LENTA → TELEGRAM (QWEN RANKING)")
    safe_log("=" * 60)

    if not all([HF_TOKEN, TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ ОШИБКА: Отсутствуют переменные окружения!")
        safe_log("   Установите: HF_API_TOKEN, TG_TOKEN, TG_CHAT_ID")
        return

    init_db()
    
    # Загружаем свежие новости
    articles = fetch_lenta_news()
    
    if not articles:
        safe_log("ℹ️ НЕТ НОВЫХ НОВОСТЕЙ")
        return

    # Qwen выбирает ТОП новости
    top_articles = rank_articles_with_qwen(articles)
    
    if not top_articles:
        safe_log("ℹ️ Qwen не выбрал ни одну новость")
        return
    
    # Публикуем
    sent = send_to_telegram(top_articles)
    
    safe_log("=" * 60)
    safe_log(f"✨ ЗАВЕРШЕНО! Опубликовано: {sent}/{len(top_articles)} новостей")

if __name__ == "__main__":
    main()
