import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/tmp/jeonghan_bot.db")

def get_connection():
    if not DB_PATH.startswith("file:"):
        db_uri = f"file:{os.path.abspath(DB_PATH)}?nolock=1"
    else:
        db_uri = DB_PATH
    return sqlite3.connect(db_uri, uri=True)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Sent Tweets table (for deduplication and history)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_tweets (
            tweet_id TEXT PRIMARY KEY,
            author_handle TEXT,
            content TEXT,
            translated_content TEXT,
            media_urls TEXT,
            category TEXT,
            created_at TIMESTAMP,
            sent_at TIMESTAMP,
            telegram_message_id INTEGER
        )
    ''')
    
    # Custom Templates table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS templates (
            category_key TEXT PRIMARY KEY,
            header TEXT,
            body TEXT,
            footer TEXT
        )
    ''')
    
    # Search Cache / Index
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT,
            results_json TEXT,
            created_at TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def is_tweet_sent(tweet_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM sent_tweets WHERE tweet_id = ?", (tweet_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def save_sent_tweet(tweet_id, author_handle, content, translated_content, media_urls, category, created_at, telegram_message_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO sent_tweets 
        (tweet_id, author_handle, content, translated_content, media_urls, category, created_at, sent_at, telegram_message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        tweet_id, 
        author_handle, 
        content, 
        translated_content, 
        json.dumps(media_urls) if isinstance(media_urls, list) else media_urls, 
        category, 
        created_at, 
        datetime.now().isoformat(),
        telegram_message_id
    ))
    conn.commit()
    conn.close()

def search_sent_tweets(query=None, date_str=None):
    conn = get_connection()
    cursor = conn.cursor()
    
    sql = "SELECT tweet_id, author_handle, content, translated_content, media_urls, category, created_at FROM sent_tweets WHERE 1=1"
    params = []
    
    if query:
        sql += " AND (content LIKE ? OR translated_content LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])
        
    if date_str:
        sql += " AND created_at LIKE ?"
        params.append(f"{date_str}%")
        
    sql += " ORDER BY created_at ASC"
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({
            "tweet_id": r[0],
            "author_handle": r[1],
            "content": r[2],
            "translated_content": r[3],
            "media_urls": json.loads(r[4]) if r[4] else [],
            "category": r[5],
            "created_at": r[6]
        })
    return results

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
