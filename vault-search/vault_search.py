#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = ["sqlite-vec"]
# ///
"""Obsidian Vault セマンティック検索。

  vault_search.py index            # 増分インデックス(初回はフル)
  vault_search.py index --full     # 全再構築
  vault_search.py search <query>   # 意味検索(-k で件数、既定8)

埋め込みは Ollama の embeddinggemma(768次元)、格納は sqlite-vec。
`ai: false` のノートと .trash/.obsidian/template/attachments は索引しない。
"""

import argparse
import json
import re
import sqlite3
import struct
import sys
import urllib.request
from pathlib import Path

import sqlite_vec
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
DB_PATH = Path(__file__).parent / "vault.db"
OLLAMA_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "embeddinggemma"
EMBED_DIM = 768
EXCLUDE_DIRS = {".trash", ".obsidian", ".grok", ".claude", "template", "attachments"}
CHUNK_MAX = 1200  # embeddinggemma のコンテキスト(2048tok)に日本語で安全に収まる長さ
MIN_CHUNK = 30


def embed(texts: list[str]) -> list[list[float]]:
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps({"model": EMBED_MODEL, "input": texts}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as res:
        return json.loads(res.read())["embeddings"]


def serialize(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """frontmatter を dict で返し、本文を切り出す。YAML の完全解釈はしない。"""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm, text[end + 4 :]


def chunk_note(body: str) -> list[tuple[str, str]]:
    """(見出しパス, チャンク本文) のリストに分割。見出し境界を優先し CHUNK_MAX で切る。"""
    chunks = []
    heading = ""
    buf: list[str] = []

    def flush():
        text = "\n".join(buf).strip()
        if len(text) >= MIN_CHUNK:
            chunks.append((heading, text))
        buf.clear()

    for line in body.splitlines():
        if re.match(r"^#{1,6}\s", line):
            flush()
            heading = line.lstrip("#").strip()
        buf.append(line)
        if sum(len(l) for l in buf) > CHUNK_MAX:
            flush()
    flush()
    return chunks


def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL);
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY, path TEXT, title TEXT, heading TEXT, content TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec
            USING vec0(embedding float[{EMBED_DIM}]);
        """
    )
    return db


def iter_notes():
    for p in VAULT.rglob("*.md"):
        rel = p.relative_to(VAULT)
        if rel.parts[0] in EXCLUDE_DIRS:
            continue
        yield p, str(rel)


def remove_file(db: sqlite3.Connection, rel: str):
    db.execute(
        "DELETE FROM chunks_vec WHERE rowid IN (SELECT id FROM chunks WHERE path = ?)",
        (rel,),
    )
    db.execute("DELETE FROM chunks WHERE path = ?", (rel,))
    db.execute("DELETE FROM files WHERE path = ?", (rel,))


def cmd_index(full: bool):
    db = open_db()
    if full:
        db.executescript("DELETE FROM files; DELETE FROM chunks; DELETE FROM chunks_vec;")
    known = dict(db.execute("SELECT path, mtime FROM files"))
    seen = set()
    n_updated = n_chunks = 0

    for path, rel in iter_notes():
        seen.add(rel)
        mtime = path.stat().st_mtime
        if known.get(rel) == mtime:
            continue

        fm, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        remove_file(db, rel)
        if fm.get("ai") == "false":
            db.execute("INSERT INTO files VALUES (?, ?)", (rel, mtime))
            continue

        title = fm.get("title") or path.stem
        chunks = chunk_note(body)
        if chunks:
            docs = [f"title: {title} | text: {c}" for _, c in chunks]
            vecs = embed(docs)
            for (heading, content), vec in zip(chunks, vecs):
                cur = db.execute(
                    "INSERT INTO chunks (path, title, heading, content) VALUES (?, ?, ?, ?)",
                    (rel, title, heading, content),
                )
                db.execute(
                    "INSERT INTO chunks_vec (rowid, embedding) VALUES (?, ?)",
                    (cur.lastrowid, serialize(vec)),
                )
            n_chunks += len(chunks)
        db.execute("INSERT INTO files VALUES (?, ?)", (rel, mtime))
        n_updated += 1
        print(f"  indexed: {rel} ({len(chunks)} chunks)")

    for rel in set(known) - seen:
        remove_file(db, rel)
        print(f"  removed: {rel}")

    db.commit()
    total = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
    print(f"done: {n_updated} files updated (+{n_chunks} chunks), index total {total} chunks")


def cmd_search(query: str, k: int):
    db = open_db()
    qvec = embed([f"task: search result | query: {query}"])[0]
    rows = db.execute(
        """
        SELECT c.path, c.title, c.heading, c.content, v.distance
        FROM chunks_vec v JOIN chunks c ON c.id = v.rowid
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
        """,
        (serialize(qvec), k),
    ).fetchall()

    for path, title, heading, content, dist in rows:
        loc = f"{path}" + (f" › {heading}" if heading else "")
        snippet = re.sub(r"\s+", " ", content)[:160]
        print(f"[{dist:.3f}] {loc}\n         {snippet}\n")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_idx = sub.add_parser("index")
    p_idx.add_argument("--full", action="store_true")
    p_s = sub.add_parser("search")
    p_s.add_argument("query")
    p_s.add_argument("-k", type=int, default=8)
    args = ap.parse_args()

    if args.cmd == "index":
        cmd_index(args.full)
    else:
        cmd_search(args.query, args.k)


if __name__ == "__main__":
    main()
