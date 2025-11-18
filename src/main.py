import os
import requests
import feedparser
import sqlite3
import time

TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
HF_TOKEN = os.getenv("HF_API_TOKEN")
RSS_URL = "https://lenta.ru/rss/news/world"
DB = "data/sent_links.db"
TOP_N = 4  # Сколько новостей выбирать Qwen

os.makedirs("data", exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB)
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
    conn = sqlite3.connect(DB)
    result = conn.execute("SELECT 1 FROM sent WHERE url=?", (url,)).fetchone()
    conn.close()
    return result is not None

def mark_sent(url, title):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, datetime())", (url, title))
    conn.commit()
    conn.close()

def fetch_all_news():
    feed = feedparser.parse(RSS_URL)
    articles = []
    for entry in feed.entries:
        title = entry.get("title") or ""
        link = entry.get("link") or ""
        desc = entry.get("summary") or ""
        if not title or not link or len(desc) < 20:
            continue
        if was_sent(link):
            continue
        articles.append({"title": title, "desc": desc, "url": link})
    return articles

def qwen_rank(articles, n=TOP_N):
    prompt = "Выбери самые важные мировые новости из списка ниже. Ответь только номерами через запятую.\n\n"
    lst = [f"{i+1}) {a['title']} {a['desc'][:120]}" for i,a in enumerate(articles[:20])]
    prompt += "\n".join(lst)
    prompt += "\n\nОтвет:"
    resp = requests.post(
        "https://api-inference.huggingface.co/models/Qwen/Qwen1.5-7B-Chat",
        headers={"Authorization": f"Bearer {HF_TOKEN}"},
        json={"inputs": prompt, "parameters": {"max_new_tokens": 20, "do_sample": False}},
        timeout=30
    )
    ids = []
    if resp.status_code == 200:
        text = resp.json()[0].get("generated_text","")
        nums = [int(s) for s in text.split(",") if s.strip().isdigit()]
        for i in nums:
            if 1 <= i <= len(articles): ids.append(i-1)
    return [articles[i] for i in ids[:n]] if ids else articles[:n]

def qwen_rewrite(title, text):
    prompt = f"Перепиши новость одним-двумя предложениями по-русски, по-человечески.\nЗаголовок: {title}\nТекст: {text}\nОтвет:"
    for attempt in range(2):
        try:
            resp = requests.post(
                "https://api-inference.huggingface.co/models/Qwen/Qwen1.5-7B-Chat",
                headers={"Authorization": f"Bearer {HF_TOKEN}"},
                json={"inputs": prompt, "parameters": {"max_new_tokens": 80, "do_sample": True}},
                timeout=20
            )
            if resp.status_code == 200:
                gen = resp.json()[0].get("generated_text","").strip()
                gen = gen.split("Ответ:")[-1].strip() if "Ответ:" in gen else gen
                if len(gen) > 15: return gen
        except Exception: pass
    return text[:200]

def send_to_telegram(art):
    msg = f"<b>{art['title']}</b>\n\n{art['summary']}\n<a href=\"{art['url']}\">Подробнее</a>"
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TG_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }, timeout=10)

def main():
    init_db()
    arts = fetch_all_news()
    if not arts:
        print("Нет новых новостей")
        return
    ranked = qwen_rank(arts, n=TOP_N)
    for art in ranked:
        art['summary'] = qwen_rewrite(art['title'], art['desc'])
        send_to_telegram(art)
        mark_sent(art["url"], art["title"])
        time.sleep(5)

if __name__ == "__main__":
    main()
