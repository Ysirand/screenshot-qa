"""题目收藏:本地 SQLite 存储,分类 / 批注 / 搜索全部在本地瞬间完成,不经过大模型。

数据放在程序目录的 collection/ 下:
    collection.db          元数据(题干、答案、批注、分类)
    images/<id>.png        题目截图原图
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import app_dir
from .logging_setup import log

DEFAULT_FOLDER = ""          # 空字符串 = 未分类
FOLDER_ALL = "\x00ALL"       # 界面用的"全部"伪分类


def collection_dir() -> Path:
    path = app_dir() / "collection"
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_dir() -> Path:
    path = collection_dir() / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return collection_dir() / "collection.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    name       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    folder     TEXT NOT NULL DEFAULT '',
    question   TEXT NOT NULL DEFAULT '',
    answer     TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    image_file TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_items_folder ON items(folder);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);
"""


class Collection:
    """收藏夹。每次操作单开连接,避免线程问题。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ---------- 分类 ----------

    def folders(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM folders ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def add_folder(self, name: str) -> bool:
        text = (name or "").strip()
        if not text:
            return False
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO folders(name, created_at) VALUES (?, ?)",
                    (text, _now()),
                )
            return True
        except sqlite3.Error as exc:
            log.warning("新建分类失败: %s", exc)
            return False

    def rename_folder(self, old: str, new: str) -> bool:
        new_text = (new or "").strip()
        if not new_text or old == new_text:
            return False
        with self._connect() as conn:
            conn.execute("UPDATE items SET folder = ? WHERE folder = ?", (new_text, old))
            conn.execute("DELETE FROM folders WHERE name = ?", (old,))
            conn.execute(
                "INSERT OR IGNORE INTO folders(name, created_at) VALUES (?, ?)",
                (new_text, _now()),
            )
        return True

    def delete_folder(self, name: str) -> None:
        """删分类,里面的题目退回未分类(不删题目)。"""
        with self._connect() as conn:
            conn.execute("UPDATE items SET folder = '' WHERE folder = ?", (name,))
            conn.execute("DELETE FROM folders WHERE name = ?", (name,))

    def ensure_folder(self, name: str) -> None:
        if (name or "").strip():
            self.add_folder(name)

    # ---------- 题目 ----------

    def add_item(
        self,
        question: str,
        answer: str,
        *,
        folder: str = DEFAULT_FOLDER,
        note: str = "",
        source: str = "",
        image_png: bytes | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO items(created_at, folder, question, answer, note, source, image_file)"
                " VALUES (?, ?, ?, ?, ?, ?, '')",
                (_now(), (folder or "").strip(), question or "", answer or "", note or "", source or ""),
            )
            item_id = int(cursor.lastrowid or 0)

        if image_png and item_id:
            name = str(item_id) + ".png"
            try:
                (image_dir() / name).write_bytes(image_png)
                with self._connect() as conn:
                    conn.execute("UPDATE items SET image_file = ? WHERE id = ?", (name, item_id))
            except OSError as exc:
                log.warning("保存题目截图失败: %s", exc)

        if folder:
            self.ensure_folder(folder)
        return item_id

    def update_item(self, item_id: int, **fields: Any) -> None:
        allowed = {"folder", "question", "answer", "note"}
        sets, values = [], []
        for key, value in fields.items():
            if key in allowed:
                sets.append(key + " = ?")
                values.append("" if value is None else str(value))
        if not sets:
            return
        values.append(int(item_id))
        with self._connect() as conn:
            conn.execute("UPDATE items SET " + ", ".join(sets) + " WHERE id = ?", values)
        folder = fields.get("folder")
        if folder:
            self.ensure_folder(str(folder))

    def delete_item(self, item_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT image_file FROM items WHERE id = ?", (int(item_id),)).fetchone()
            conn.execute("DELETE FROM items WHERE id = ?", (int(item_id),))
        if row and row["image_file"]:
            try:
                (image_dir() / row["image_file"]).unlink(missing_ok=True)
            except OSError:
                pass

    def get_item(self, item_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM items WHERE id = ?", (int(item_id),)).fetchone()

    def items(self, folder: str | None = None, keyword: str = "", limit: int = 1000) -> list[sqlite3.Row]:
        sql = "SELECT * FROM items"
        where, params = [], []
        if folder not in (None, FOLDER_ALL):
            if folder == DEFAULT_FOLDER:
                where.append("folder = ''")
            else:
                where.append("folder = ?")
                params.append(folder)
        text = (keyword or "").strip()
        if text:
            like = "%" + text + "%"
            where.append("(question LIKE ? OR answer LIKE ? OR note LIKE ?)")
            params.extend([like, like, like])
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()

    def counts(self) -> dict[str, int]:
        """每个分类有多少题(含未分类和总数)。"""
        result = {DEFAULT_FOLDER: 0}
        with self._connect() as conn:
            for row in conn.execute("SELECT folder, COUNT(*) AS n FROM items GROUP BY folder"):
                result[row["folder"]] = int(row["n"])
            total = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
        result[FOLDER_ALL] = int(total["n"]) if total else 0
        return result

    def image_path(self, item_id: int) -> Path | None:
        row = self.get_item(item_id)
        if not row or not row["image_file"]:
            return None
        path = image_dir() / row["image_file"]
        return path if path.exists() else None


TITLE_MARK = "【题目】"


def extract_title(answer: str, fallback: str = "") -> str:
    """给收藏列表取个看得懂的标题。

    优先用模型回答里那行【题目】(提示词里让它顺手给的),
    没有就退而用答案第一行(通常是【答案】xxx),再没有就用传入的兜底值。
    """
    for line in (answer or "").splitlines():
        text = line.strip().lstrip("-*># ").strip()
        if text.startswith(TITLE_MARK):
            title = text[len(TITLE_MARK):].strip(" :：,、.")
            if title:
                return title[:60]
    for line in (answer or "").splitlines():
        text = line.strip().lstrip("-*># ").strip()
        if text:
            return text[:60]
    return (fallback or "").strip()[:60] or "(未识别题干)"


def _now() -> str:
    return datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")