import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS images (
                    unique_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    features TEXT,
                    posted INTEGER NOT NULL DEFAULT 0
                )
            """)

    def connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def add_image(self, path, kind, unique_id):
        with self.connect() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO images(unique_id,path,kind) VALUES(?,?,?)",
                (unique_id, path, kind)
            )
            return cur.rowcount > 0

    def set_features(self, unique_id, features):
        with self.connect() as con:
            con.execute(
                "UPDATE images SET features=? WHERE unique_id=?",
                (json.dumps(features), unique_id)
            )

    def get_images(self, kind):
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM images WHERE kind=? AND posted=0 ORDER BY rowid",
                (kind,)
            ).fetchall()
        return [
            {
                "unique_id": r["unique_id"],
                "path": r["path"],
                "features": json.loads(r["features"]) if r["features"] else None,
            }
            for r in rows
        ]

    def count(self, kind):
        with self.connect() as con:
            return con.execute(
                "SELECT COUNT(*) FROM images WHERE kind=? AND posted=0", (kind,)
            ).fetchone()[0]

    def mark_posted(self, unique_id):
        with self.connect() as con:
            con.execute("UPDATE images SET posted=1 WHERE unique_id=?", (unique_id,))

    def delete_image(self, unique_id):
        with self.connect() as con:
            con.execute("DELETE FROM images WHERE unique_id=?", (unique_id,))


    def get_posted_features(self):
        with self.connect() as con:
            rows = con.execute(
                "SELECT features FROM images WHERE posted=1 AND features IS NOT NULL"
            ).fetchall()
        return [json.loads(r["features"]) for r in rows]


    def add_chat_message(self, chat_id, role, content):
        with self.connect() as con:
            con.execute(
                "INSERT INTO chat_history(chat_id,role,content) VALUES(?,?,?)",
                (chat_id, role, content)
            )

    def get_chat_history(self, chat_id, limit=12):
        with self.connect() as con:
            rows = con.execute(
                "SELECT role,content FROM chat_history WHERE chat_id=? "
                "ORDER BY id DESC LIMIT ?",
                (chat_id, limit)
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear_chat_history(self, chat_id):
        with self.connect() as con:
            con.execute("DELETE FROM chat_history WHERE chat_id=?", (chat_id,))
