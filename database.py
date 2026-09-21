from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite


@dataclass(frozen=True)
class QuizState:
    question_index: int
    score: int
    status: str


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quiz_sessions (
                    user_id INTEGER PRIMARY KEY,
                    question_index INTEGER NOT NULL DEFAULT 0,
                    score INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    zone TEXT,
                    completed_at TEXT,
                    checkout_started_at TEXT,
                    reminder_sent_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
                """
            )
            await db.commit()

    async def start_quiz(self, user_id: int, username: str | None, first_name: str | None) -> None:
        now = _utc_now()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO users(user_id, username, first_name, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       username=excluded.username,
                       first_name=excluded.first_name,
                       updated_at=excluded.updated_at""",
                (user_id, username, first_name, now),
            )
            await db.execute(
                """INSERT INTO quiz_sessions(user_id, question_index, score, status)
                   VALUES (?, 0, 0, 'active')
                   ON CONFLICT(user_id) DO UPDATE SET
                       question_index=0,
                       score=0,
                       status='active',
                       zone=NULL,
                       completed_at=NULL,
                       checkout_started_at=NULL,
                       reminder_sent_at=NULL""",
                (user_id,),
            )
            await db.commit()

    async def get_state(self, user_id: int) -> QuizState | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT question_index, score, status FROM quiz_sessions WHERE user_id=?",
                (user_id,),
            )
            row = await cursor.fetchone()
        return QuizState(*row) if row else None

    async def add_answer(self, user_id: int, expected_index: int, points: int) -> QuizState | None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """UPDATE quiz_sessions
                   SET score=score+?, question_index=question_index+1
                   WHERE user_id=? AND status='active' AND question_index=?""",
                (points, user_id, expected_index),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return None
            cursor = await db.execute(
                "SELECT question_index, score, status FROM quiz_sessions WHERE user_id=?",
                (user_id,),
            )
            row = await cursor.fetchone()
            await db.commit()
        return QuizState(*row)

    async def complete_quiz(self, user_id: int, zone: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE quiz_sessions
                   SET status='completed', zone=?, completed_at=?
                   WHERE user_id=? AND status='active'""",
                (zone, _utc_now(), user_id),
            )
            await db.commit()

    async def mark_checkout_started(self, user_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE quiz_sessions SET checkout_started_at=?
                   WHERE user_id=? AND status='completed'""",
                (_utc_now(), user_id),
            )
            await db.commit()

    async def reminder_candidates(self) -> list[int]:
        threshold = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """SELECT user_id FROM quiz_sessions
                   WHERE status='completed'
                     AND completed_at <= ?
                     AND checkout_started_at IS NULL
                     AND reminder_sent_at IS NULL""",
                (threshold,),
            )
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def mark_reminder_sent(self, user_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE quiz_sessions SET reminder_sent_at=? WHERE user_id=?",
                (_utc_now(), user_id),
            )
            await db.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

