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

# ---------- ВРЕМЯ И ФИЛЬТР ЗА ПОСЛЕДНИЙ ЧАС ----------

def parse_rss_time(time_str):
    """
    pubDate в RSS: 'Tue, 18 Nov 2025 19:18:00 +0300'
    Переводим в datetime в МСК (без tzinfo), чтобы сравнивать с now (тоже МСК).
    """
    try:
        dt = parsedate_to_datetime(time_str)  # aware datetime
        # GitHub runner в UTC, но Lenta даёт +0300 (MSK),
        # нам удобно работать в МСК без tzinfo:
        dt = dt.astimezone().astimezone()  # просто убедимся, что aware
        # Оставляем локальное время, но без tzinfo (как "на стене часов"):
        dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        return None

def is_within_last_hour(article_time):
    """
    Проверяем, что новость попадает в интервал [now-1h, now).
    Работает в "локальном" времени (как видит GitHub + наш dt без tzinfo).
    """
    if not article_time:
        # Если время не распарсили — не отбрасываем, лучше отдать на ИИ
        return True

    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    return one_hour_ago <= article_time < now

def fetch_lenta_last_hour():
    """
    Загружает новости из RSS и оставляет только те,
    что опубликованы за последний час.
    """
    safe_log("📰 Загрузка новостей за последний час...")
    feed = feedparser.parse(RSS_URL)
    articles = []

    for entry in feed.entries[:100]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        desc = (entry.get("summary") or "")[:400].strip()

        # Время публикации
        published = entry.get("published") or entry.get("pubDate") or ""
        article_time = parse_rss_time(published)

        if not is_within_last_hour(article_time):
            continue

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

# ---------- QWEN: РАНЖИРОВАНИЕ ----------

def rank_articles_with_ai(articles):
    """
    Qwen выбирает топ 3-5 новостей.
    Если HF_TOKEN нет или что-то падает — возвращаем первые до 5.
    """
    if not articles or not HF_TOKEN:
        return articles[:5]

    if len(articles) <= 5:
        return articles

    safe_log(f"🤖 ИИ ранжирует {len(articles)} новостей...")

    # Берём максимум первые 20 для промпта
    subset = articles[:20]
    items_text = "\n".join(
        f"{i+1}. [{a['title']}] {a['desc'][:120]}"
        for i, a in enumerate(subset)
    )

    prompt = f"""Ты опытный редактор новостного Telegram-канала.
Из списка ниже выбери 3-5 САМЫХ ВАЖНЫХ новостей.

Критерии важности:
- Влияние на большое количество людей
- Политика, экономика, войны, ЧП, громкие расследования
- Высокий интерес аудитории

Список новостей:
{items_text}

Ответь ТОЛЬКО НОМЕРАМИ через запятую (например: 1,3,5,7)."""

    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 50,
                    "temperature": 0.3,
                    "do_sample": False
                }
            },
            timeout=25
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                text = data[0].get("generated_text", "").strip()
                # Берём последнюю строку как ответ
                line = text.split("\n")[-1]
                nums = []
                for part in line.replace(" ", "").split(","):
                    if part.isdigit():
                        idx = int(part) - 1
                        if 0 <= idx < len(subset):
                            nums.append(idx)
                nums = list(dict.fromkeys(nums))  # убираем дубликаты
                if nums:
                    chosen = [subset[i] for i in nums]
                    safe_log(f"✓ ИИ выбрал новости: {[i+1 for i in nums]}")
                    return chosen

    except Exception as e:
        safe_log(f"⚠️ Ошибка ранжирования: {str(e)[:80]}")

    # Fallback
    return articles[:5]

# ---------- QWEN: ПЕРЕПИСЬ НОВОСТИ ----------

def rewrite_with_hf(title, text):
    """
    Qwen переписывает новость в 2–3 предложения.
    """
    if not HF_TOKEN:
        return text[:180]

    prompt = f"""Перепиши новостной текст на русском языке в 2–3 коротких предложения.
Сделай формулировку живой и понятной, НЕ копируй исходный текст дословно.

Заголовок: {title}
Текст: {text}

Ответ: только переписанный текст, без пояснений и без лишних комментариев."""

    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 120,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "do_sample": True
                }
            },
            timeout=25
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                result = data[0].get("generated_text", "").strip()
                # Отрезаем промпт, если модель его повторила
                if prompt in result:
                    result = result.split(prompt)[-1].strip()
                # Берём 2–3 предложения
                sentences = [s.strip() for s in result.split(".") if s.strip()]
                result = ". ".join(sentences[:3]) + "."
                result = re.sub(r'\d+$', '', result).strip()
                if len(result) > 30:
                    return result[:400]
    except Exception as e:
        safe_log(f"⚠️ HF ошибка: {str(e)[:80]}")

    return text[:180]

# ---------- КАРТИНКА ----------

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

# ---------- ОТПРАВКА В TELEGRAM ----------

def send_to_telegram(articles):
    if not articles:
        safe_log("⚠️ НЕТ НОВОСТЕЙ ДЛЯ ПУБЛИКАЦИИ")
        return 0

    safe_log(f"📤 Публикую {len(articles)} новостей...\n")
    sent = 0

    for i, art in enumerate(articles, 1):
        title = art["title"]
        summary = rewrite_with_hf(title, art["desc"])

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
                    requests.post(
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
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={
                        "chat_id": TG_CHAT_ID,
                        "text": msg,
                        "parse_mode": "Markdown"
                    },
                    timeout=15
                )

            safe_log(f"✓ [{i}] {title[:50]}...")
            mark_sent(art["url"], art["title"], summary)
            sent += 1

            if i < len(articles):
                # Для GitHub Actions можно оставить 10–30 секунд,
                # на VPS можно сделать 300–600 (5–10 минут)
                time.sleep(10)

        except Exception as e:
            safe_log(f"✗ [{i}] Ошибка отправки: {str(e)[:80]}")

    return sent

# ---------- MAIN ----------

def main():
    safe_log("🚀 LENTA → TELEGRAM (QWEN, LAST HOUR)")
    safe_log("⏰ Анализ новостей за последний час...\n")

    if not all([HF_TOKEN, TG_TOKEN, TG_CHAT_ID]):
        safe_log("❌ НЕТ СЕКРЕТОВ HF_API_TOKEN / TG_TOKEN / TG_CHAT_ID")
        return

    init_db()
    articles = fetch_lenta_last_hour()

    if not articles:
        safe_log("ℹ️ НЕТ НОВОСТЕЙ ЗА ПОСЛЕДНИЙ ЧАС")
        return

    top_articles = rank_articles_with_ai(articles)
    sent = send_to_telegram(top_articles)
    safe_log(f"\n✨ ГОТОВО! Опубликовано: {sent}/{len(top_articles)}")

if __name__ == "__main__":
    main()
