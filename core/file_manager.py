import os
import sqlite3
import numpy as np
from datetime import datetime
from langchain_ollama import OllamaEmbeddings
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent_files.db")
WATCHED_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "agent_workspace")

embedder = OllamaEmbeddings(model="nomic-embed-text-v2-moe")

# supported file types and how to read them
SUPPORTED_EXTENSIONS = {".txt", ".md", ".py", ".js", ".json", ".csv", ".pdf", ".docx", ".xlsx", ".pptx"}

def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            start += chunk_size - overlap  # step back by overlap amount
        return chunks
def index_file(filepath: str) -> str:
    # step 1 — read the file
    content = read_file_content(filepath)  
    if not content:
        return f"Could not read {filepath}"

    # step 2 — split into chunks
    chunks = _chunk_text(content, chunk_size=500, overlap=50)

    # step 3 + 4 — embed each chunk and store it
    conn = _get_conn()
    
    # first store file metadata, get back document_id
    conn.execute(
        "INSERT OR REPLACE INTO documents (filepath, filename, filetype, indexed_at) VALUES (?, ?, ?, ?)",
        (filepath, os.path.basename(filepath), filepath.split(".")[-1], datetime.now().isoformat())
    )
    doc_id = conn.execute(
        "SELECT id FROM documents WHERE filepath = ?", (filepath,)
    ).fetchone()[0]
    
    # delete old chunks if re-indexing
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
    
    # embed and store each chunk
    for i, chunk in enumerate(chunks):
        embedding = embedder.embed_query(chunk)
        embedding_bytes = np.array(embedding, dtype=np.float32).tobytes()
        conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
            (doc_id, i, chunk, embedding_bytes)
        )
    
    conn.commit()
    conn.close()
    return f"Indexed {os.path.basename(filepath)} — {len(chunks)} chunks"

class AgentFileHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory:
            index_file(event.src_path)  # re-embed the changed file
    
    def on_created(self, event):
        if not event.is_directory:
            index_file(event.src_path)
    
    def on_deleted(self, event):
        if not event.is_directory:
            remove_file_from_index(event.src_path)