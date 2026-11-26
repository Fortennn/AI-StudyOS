import sqlite3
import os
import json
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
from app.models import StudyKitResponse, SessionRecord, ChatMessage
from app.telemetry import logger

class Storage:
    def __init__(self, db_path: str = "data/study.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    text_length INTEGER NOT NULL,
                    card_count INTEGER NOT NULL,
                    quiz_count INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    raw_text TEXT DEFAULT '',
                    chat_json TEXT DEFAULT '[]',
                    pinned INTEGER DEFAULT 0,
                    user_id TEXT DEFAULT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    google_id TEXT,
                    email TEXT
                )
            """)
            u_cols = [r[1] for r in cursor.execute("PRAGMA table_info(users)").fetchall()]
            if "google_id" not in u_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
            if "email" not in u_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
            cols = [r[1] for r in cursor.execute("PRAGMA table_info(sessions)").fetchall()]
            if "raw_text" not in cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN raw_text TEXT DEFAULT ''")
            if "chat_json" not in cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN chat_json TEXT DEFAULT '[]'")
            if "pinned" not in cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0")
            if "user_id" not in cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT DEFAULT NULL")
            conn.commit()
            logger.info("Storage initialized successfully")

    @staticmethod
    def _owner_clause(user_id: Optional[str]) -> Tuple[str, Tuple[Any, ...]]:
        if user_id:
            return "user_id = ?", (user_id,)
        return "user_id IS NULL", ()

    # --- Users ---
    def create_user(self, user_id: str, username: str, password_hash: str, salt: str) -> Dict[str, str]:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, username.lower(), password_hash, salt, datetime.utcnow().isoformat())
            )
            conn.commit()
        return {"id": user_id, "username": username}

    def get_user_by_username(self, username: str) -> Optional[Dict[str, str]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, salt FROM users WHERE LOWER(username) = LOWER(?)",
                (username,)
            ).fetchone()
            if not row:
                return None
            return {"id": row[0], "username": row[1], "password_hash": row[2], "salt": row[3]}

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, str]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return None
            return {"id": row[0], "username": row[1]}

    def upsert_google_user(self, google_id: str, email: str, name: str = "") -> Dict[str, str]:
        import uuid as _uuid
        import re as _re
        with self._get_connection() as conn:
            row = conn.execute("SELECT id, username FROM users WHERE google_id = ?", (google_id,)).fetchone()
            if row:
                return {"id": row[0], "username": row[1]}

            if email:
                row_email = conn.execute("SELECT id, username FROM users WHERE LOWER(email) = LOWER(?)", (email,)).fetchone()
                if row_email:
                    conn.execute("UPDATE users SET google_id = ? WHERE id = ?", (google_id, row_email[0]))
                    conn.commit()
                    return {"id": row_email[0], "username": row_email[1]}

            base_raw = (email.split("@")[0] if email else name) or "user"
            base = _re.sub(r"[^a-zA-Z0-9_.-]", "", base_raw).lower()[:24] or "user"
            candidate = base
            suffix = 1
            while conn.execute("SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)", (candidate,)).fetchone():
                candidate = f"{base[:20]}_{suffix}"
                suffix += 1

            uid = f"usr-{_uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO users (id, username, password_hash, salt, created_at, google_id, email) VALUES (?, ?, '', '', ?, ?, ?)",
                (uid, candidate, datetime.utcnow().isoformat(), google_id, email)
            )
            conn.commit()
            return {"id": uid, "username": candidate}

    def is_healthy(self) -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"Storage health check failed: {e}")
            return False

    def save_session(self, kit: StudyKitResponse, raw_text: str, user_id: Optional[str] = None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (id, created_at, topic, text_length, card_count, quiz_count, data_json, raw_text, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kit.request_id,
                datetime.utcnow().isoformat(),
                kit.topic,
                len(raw_text),
                len(kit.flashcards),
                len(kit.quiz),
                kit.model_dump_json(),
                raw_text[:60000],
                user_id
            ))
            conn.commit()
            logger.info(f"Saved session {kit.request_id} to database")

    def get_session(self, session_id: str, user_id: Optional[str] = None) -> Optional[StudyKitResponse]:
        clause, params = self._owner_clause(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT data_json, chat_json FROM sessions WHERE id = ? AND {clause}", (session_id, *params))
            row = cursor.fetchone()
            if row:
                data = json.loads(row[0])
                chat_raw = json.loads(row[1] or "[]")
                data["chat_history"] = chat_raw
                return StudyKitResponse(**data)
            return None

    def get_session_text(self, session_id: str, user_id: Optional[str] = None) -> str:
        clause, params = self._owner_clause(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT raw_text FROM sessions WHERE id = ? AND {clause}", (session_id, *params))
            row = cursor.fetchone()
            return (row[0] or "") if row else ""

    def append_chat_exchange(self, session_id: str, question: str, answer: str, user_id: Optional[str] = None):
        if not session_id:
            return
        clause, params = self._owner_clause(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT chat_json FROM sessions WHERE id = ? AND {clause}", (session_id, *params))
            row = cursor.fetchone()
            if not row:
                return
            history = json.loads(row[0] or "[]")
            history.append({"role": "user", "text": question})
            history.append({"role": "assistant", "text": answer})
            cursor.execute(
                f"UPDATE sessions SET chat_json = ? WHERE id = ? AND {clause}",
                (json.dumps(history, ensure_ascii=False), session_id, *params)
            )
            conn.commit()

    def clear_chat_history(self, session_id: str, user_id: Optional[str] = None):
        if not session_id:
            return
        clause, params = self._owner_clause(user_id)
        with self._get_connection() as conn:
            conn.execute(f"UPDATE sessions SET chat_json = '[]' WHERE id = ? AND {clause}", (session_id, *params))
            conn.commit()

    def list_recent_sessions(self, limit: int = 30, user_id: Optional[str] = None) -> List[SessionRecord]:
        clause, params = self._owner_clause(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT id, created_at, topic, text_length, card_count, quiz_count, COALESCE(pinned, 0)
                FROM sessions
                WHERE {clause}
                ORDER BY COALESCE(pinned, 0) DESC, created_at DESC
                LIMIT ?
            """, (*params, limit))
            rows = cursor.fetchall()
            return [
                SessionRecord(
                    id=r[0],
                    created_at=r[1],
                    topic=r[2],
                    text_length=r[3],
                    card_count=r[4],
                    quiz_count=r[5],
                    pinned=bool(r[6])
                )
                for r in rows
            ]

    def delete_session(self, session_id: str, user_id: Optional[str] = None) -> bool:
        clause, params = self._owner_clause(user_id)
        with self._get_connection() as conn:
            cur = conn.execute(f"DELETE FROM sessions WHERE id = ? AND {clause}", (session_id, *params))
            conn.commit()
            return cur.rowcount > 0

    def toggle_pin_session(self, session_id: str, user_id: Optional[str] = None) -> Optional[bool]:
        clause, params = self._owner_clause(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COALESCE(pinned, 0) FROM sessions WHERE id = ? AND {clause}", (session_id, *params))
            row = cursor.fetchone()
            if not row:
                return None
            new_val = 0 if row[0] else 1
            cursor.execute(f"UPDATE sessions SET pinned = ? WHERE id = ? AND {clause}", (new_val, session_id, *params))
            conn.commit()
            return bool(new_val)
