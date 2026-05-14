"""
하네스 엔지니어링 전용 SQLite 데이터베이스.

에피소드 메트릭과 학습된 전술 규칙을 영속 저장합니다.
DB 경로: data/harness.db
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .metrics import EpisodeMetrics

logger = logging.getLogger(__name__)

_HARNESS_DB_PATH = Path(__file__).parent.parent.parent / "data" / "harness.db"


class HarnessDB:
    """
    하네스 전용 SQLite 저장소.

    테이블:
        episodes: 에피소드 결과 및 메트릭 JSON
        rules:    추출된 전술 규칙 및 효과 통계
    """

    _CREATE_EPISODES = """
    CREATE TABLE IF NOT EXISTS episodes (
        episode_id      TEXT PRIMARY KEY,
        timestamp       TEXT NOT NULL,
        winner          TEXT NOT NULL,
        metrics_json    TEXT NOT NULL,
        active_rules_json TEXT NOT NULL DEFAULT '[]'
    )
    """

    _CREATE_RULES = """
    CREATE TABLE IF NOT EXISTS rules (
        rule_id         TEXT PRIMARY KEY,
        text            TEXT NOT NULL,
        section         TEXT NOT NULL,
        confidence      REAL NOT NULL DEFAULT 0.5,
        win_count       INT  NOT NULL DEFAULT 0,
        loss_count      INT  NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        active          INT  NOT NULL DEFAULT 1,
        source_episode  TEXT NOT NULL DEFAULT ''
    )
    """

    def __init__(self, db_path: Path = _HARNESS_DB_PATH):
        """
        HarnessDB 초기화.

        Args:
            db_path: SQLite 데이터베이스 파일 경로
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()
        logger.info(f"HarnessDB 초기화 완료: {self._path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.execute(self._CREATE_EPISODES)
            conn.execute(self._CREATE_RULES)

    # ── 에피소드 CRUD ─────────────────────────────────────────────

    def save_episode(self, metrics: EpisodeMetrics, active_rule_ids: List[str]):
        """
        에피소드 메트릭을 DB에 저장합니다.

        Args:
            metrics: EpisodeMetrics 인스턴스
            active_rule_ids: 이 에피소드에서 활성화된 규칙 ID 목록
        """
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO episodes
                       (episode_id, timestamp, winner, metrics_json, active_rules_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        metrics.episode_id,
                        metrics.timestamp,
                        metrics.winner,
                        json.dumps(metrics.to_dict(), ensure_ascii=False),
                        json.dumps(active_rule_ids, ensure_ascii=False),
                    ),
                )
            logger.debug(f"에피소드 저장: {metrics.episode_id} ({metrics.winner})")
        except Exception as e:
            logger.error(f"save_episode 오류: {e}")

    def get_episode(self, episode_id: str) -> Optional[dict]:
        """
        특정 에피소드를 조회합니다.

        Returns:
            에피소드 딕셔너리 또는 None
        """
        try:
            with self._lock, self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
                ).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["metrics"] = json.loads(d.get("metrics_json", "{}"))
            d["active_rules"] = json.loads(d.get("active_rules_json", "[]"))
            return d
        except Exception as e:
            logger.error(f"get_episode 오류: {e}")
            return None

    def get_all_episodes(self) -> List[dict]:
        """
        모든 에피소드 목록을 반환합니다.

        Returns:
            에피소드 딕셔너리 리스트 (타임스탬프 오름차순)
        """
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM episodes ORDER BY timestamp ASC"
                ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["metrics"] = json.loads(d.get("metrics_json", "{}"))
                d["active_rules"] = json.loads(d.get("active_rules_json", "[]"))
                result.append(d)
            return result
        except Exception as e:
            logger.error(f"get_all_episodes 오류: {e}")
            return []

    def get_win_rate(self, last_n: int = 20) -> float:
        """
        최근 N 에피소드의 BLUFOR 승률을 반환합니다.

        Args:
            last_n: 집계할 최근 에피소드 수

        Returns:
            승률 (0.0 ~ 1.0)
        """
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT winner FROM episodes ORDER BY timestamp DESC LIMIT ?",
                    (last_n,)
                ).fetchall()
            if not rows:
                return 0.0
            wins = sum(1 for r in rows if r["winner"] == "BLUFOR")
            return wins / len(rows)
        except Exception as e:
            logger.error(f"get_win_rate 오류: {e}")
            return 0.0

    # ── 규칙 CRUD ─────────────────────────────────────────────────

    def save_rule(
        self,
        rule_id: str,
        text: str,
        section: str,
        confidence: float,
        source_episode: str,
    ):
        """
        새 전술 규칙을 저장합니다.

        Args:
            rule_id: 규칙 고유 ID
            text: 규칙 텍스트
            section: 규칙 분류 (RECON / ATTACK / EXECUTION / LEARNED_RULES)
            confidence: 신뢰도 (0.0 ~ 1.0)
            source_episode: 출처 에피소드 ID
        """
        try:
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._lock, self._connect() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO rules
                       (rule_id, text, section, confidence, win_count, loss_count,
                        created_at, active, source_episode)
                       VALUES (?, ?, ?, ?, 0, 0, ?, 1, ?)""",
                    (rule_id, text, section, confidence, created_at, source_episode),
                )
            logger.debug(f"규칙 저장: {rule_id} [{section}] conf={confidence:.2f}")
        except Exception as e:
            logger.error(f"save_rule 오류: {e}")

    def update_rule_effectiveness(self, rule_ids: List[str], winner: str):
        """
        에피소드 결과에 따라 규칙의 win_count / loss_count를 업데이트합니다.

        Args:
            rule_ids: 업데이트할 규칙 ID 목록
            winner: 에피소드 승자 ("BLUFOR" / "OPFOR" / "draw")
        """
        if not rule_ids:
            return
        try:
            if winner == "BLUFOR":
                col = "win_count"
            elif winner == "OPFOR":
                col = "loss_count"
            else:
                return  # draw 시 업데이트 없음

            with self._lock, self._connect() as conn:
                for rid in rule_ids:
                    conn.execute(
                        f"UPDATE rules SET {col} = {col} + 1 WHERE rule_id = ?",
                        (rid,),
                    )
            logger.debug(f"규칙 효과 업데이트: {len(rule_ids)}개, 결과={winner}")
        except Exception as e:
            logger.error(f"update_rule_effectiveness 오류: {e}")

    def get_active_rules(self, section: Optional[str] = None) -> List[dict]:
        """
        활성 규칙 목록을 반환합니다.

        Args:
            section: 특정 섹션만 필터링 (None이면 전체)

        Returns:
            규칙 딕셔너리 리스트
        """
        try:
            with self._lock, self._connect() as conn:
                if section:
                    rows = conn.execute(
                        "SELECT * FROM rules WHERE active = 1 AND section = ? ORDER BY confidence DESC",
                        (section,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM rules WHERE active = 1 ORDER BY section, confidence DESC"
                    ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_active_rules 오류: {e}")
            return []

    def deactivate_rule(self, rule_id: str):
        """
        특정 규칙을 비활성화합니다.

        Args:
            rule_id: 비활성화할 규칙 ID
        """
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE rules SET active = 0 WHERE rule_id = ?", (rule_id,)
                )
            logger.debug(f"규칙 비활성화: {rule_id}")
        except Exception as e:
            logger.error(f"deactivate_rule 오류: {e}")

    def delete_rule(self, rule_id: str):
        """
        특정 규칙을 영구 삭제합니다.

        Args:
            rule_id: 삭제할 규칙 ID
        """
        try:
            with self._lock, self._connect() as conn:
                conn.execute("DELETE FROM rules WHERE rule_id = ?", (rule_id,))
            logger.debug(f"규칙 삭제: {rule_id}")
        except Exception as e:
            logger.error(f"delete_rule 오류: {e}")

    def get_stats(self) -> dict:
        """
        DB 통계를 반환합니다.

        Returns:
            총 에피소드 수, 승률, 활성 규칙 수 등을 포함하는 딕셔너리
        """
        try:
            with self._lock, self._connect() as conn:
                total_episodes = conn.execute(
                    "SELECT COUNT(*) FROM episodes"
                ).fetchone()[0]

                win_count = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE winner = 'BLUFOR'"
                ).fetchone()[0]

                active_rules = conn.execute(
                    "SELECT COUNT(*) FROM rules WHERE active = 1"
                ).fetchone()[0]

                section_stats = conn.execute(
                    "SELECT section, COUNT(*) as cnt FROM rules WHERE active = 1 GROUP BY section"
                ).fetchall()

            win_rate = win_count / total_episodes if total_episodes > 0 else 0.0
            sections = {row["section"]: row["cnt"] for row in section_stats}

            return {
                "total_episodes": total_episodes,
                "win_count": win_count,
                "win_rate": round(win_rate, 3),
                "active_rules": active_rules,
                "rules_by_section": sections,
            }
        except Exception as e:
            logger.error(f"get_stats 오류: {e}")
            return {
                "total_episodes": 0,
                "win_count": 0,
                "win_rate": 0.0,
                "active_rules": 0,
                "rules_by_section": {},
            }
