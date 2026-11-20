import sqlite3
import os
import json
from datetime import datetime
from typing import List, Optional
from app.models import StudyKitResponse, SessionRecord
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
                    data_json TEXT NOT NULL
                )
            """)
            conn.commit()
            logger.info("Storage initialized successfully")

    def is_healthy(self) -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"Storage health check failed: {e}")
            return False

    def save_session(self, kit: StudyKitResponse, raw_text: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (id, created_at, topic, text_length, card_count, quiz_count, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                kit.request_id,
                datetime.utcnow().isoformat(),
                kit.topic,
                len(raw_text),
                len(kit.flashcards),
                len(kit.quiz),
                kit.model_dump_json()
            ))
            conn.commit()
            logger.info(f"Saved session {kit.request_id} to database")

    def get_session(self, session_id: str) -> Optional[StudyKitResponse]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data_json FROM sessions WHERE id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                data = json.loads(row[0])
                return StudyKitResponse(**data)
            return None

    def list_recent_sessions(self, limit: int = 10) -> List[SessionRecord]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, created_at, topic, text_length, card_count, quiz_count
                FROM sessions
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [
                SessionRecord(
                    id=r[0],
                    created_at=r[1],
                    topic=r[2],
                    text_length=r[3],
                    card_count=r[4],
                    quiz_count=r[5]
                )
                for r in rows
            ]
