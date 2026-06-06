import sqlite3
import numpy as np
import os
from langchain_ollama import OllamaEmbeddings
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent_memory.db")
embedder = OllamaEmbeddings(model="nomic-embed-text-v2-moe")


def _get_conn():
    return sqlite3.connect(DB_PATH, timeout=15)

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    # dot product divided by product of magnitudes
    # returns a float between -1 and 1, where 1 = identical
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)

def init_db():
    conn = _get_conn()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id         TEXT PRIMARY KEY,
            started_at TEXT,
            ended_at   TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role       TEXT,
            content    TEXT,
            timestamp  TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            embedding  BLOB
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS  documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE,
            filename TEXT,
            filetype TEXT,       
            indexed_at TEXT,
            modified_at TEXT
                   );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER,
            chunk_index INTEGER,
            content TEXT,
            embeddings BLOB           
                   );
    """)
    
    conn.commit()
    conn.close()

def save_message(session_id: str, role: str, content: str) -> int:
    conn = _get_conn()
    cursor = conn.cursor()
    
    # step 1 — save the text
    cursor.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, datetime.now().isoformat())
    )
    message_id = cursor.lastrowid  # ← this gives you the auto-generated ID
    
    # step 2 — save the embedding, but only for user messages
    if role == "user":
        embedding = embedder.embed_query(content)  # returns a list of floats
        embedding_bytes = np.array(embedding, dtype=np.float32).tobytes()
        cursor.execute(
            "INSERT INTO embeddings (message_id, embedding) VALUES (?, ?)",
            (message_id, embedding_bytes)
        )
    
    conn.commit()
    conn.close()
    return message_id

def get_recent_context(session_id: str, n: int = 10) -> list[dict]:
    conn = _get_conn()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT role, content 
        FROM messages 
        WHERE session_id != ?
        ORDER BY id DESC 
        LIMIT ?
    """, (session_id, n))
    
    rows = cursor.fetchall()
    conn.close()
    
    # reverse so it's chronological (oldest first)
    rows.reverse()
    return [{"role": row[0], "content": row[1]} for row in rows]

def search_similar(query_text: str, session_id: str, top_k: int = 5) -> list[dict]:
    # step 1 — embed the query
    query_embedding = np.array(
        embedder.embed_query(query_text), dtype=np.float32
    )
    
    # step 2 — fetch all stored embeddings with their message text
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.embedding, m.content, m.role
        FROM embeddings e
        JOIN messages m ON e.message_id = m.id
        WHERE m.session_id != ?
    """, (session_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return []
    
    # step 3 — score each one and return top_k
    scored = []
    for embedding_bytes, content, role in rows:
        stored = np.frombuffer(embedding_bytes, dtype=np.float32)
        score = _cosine_similarity(query_embedding, stored)
        scored.append({"content": content, "role": role, "score": score})
    
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]

# in memory.py
def start_session(session_id: str):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
        (session_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("DB initialized")
    
    save_message("test-session", "user", "set volume to 50")
    save_message("test-session", "assistant", "Volume set to 50%")
    save_message("test-session", "user", "open spotify")
    print("Messages saved")
    
    results = search_similar("can you change the volume", "different-session")
    for r in results:
        print(f"{r['score']:.3f} — {r['content']}")