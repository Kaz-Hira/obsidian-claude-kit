#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = ["sqlite-vec"]
# ///
"""孤立ノートに、繋ぐべきリンク先を意味の近さで提案する。

[[Vaultセマンティック検索]] が作った `vault.db`(全チャンクの埋め込み)を再利用し、
どこからもリンクされていないノートごとに「意味が近い既存ノート」を上位いくつか挙げる。
X調査ノートの定石「書いたら既存知識へリンク2〜3本」を機械が下支えする。

孤立の定義は vault-lint.py に合わせる: 構造ページ(ai: false)と daily/Inbox/Memo を除き、
どこからもリンクされていないノート。

  vault-suggest-links.py            # 孤立ノートごとに候補を提案
  vault-suggest-links.py --limit 5  # 対象を古い順/名前順に絞る

埋め込みの照会に Ollama を使う(索引済みの本文を再エンコードして近傍検索)。
索引が古いと結果もずれるので、呼び出し側で先に `vault_search.py index` を走らせる。
"""

from __future__ import annotations  # 3.9 の /usr/bin/python3 で実行されても
# `X | None` 等の PEP 604 が def 実行時に評価されないようにする(2026-07-26)。
# これが無いと py_compile は通るのに実行時 TypeError で即死する。

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
DB_PATH = _envpath("CLAUDE_DIR", "~/.claude") / "vault-search/vault.db"
OLLAMA_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "embeddinggemma"

SKIP_DIRS = {".obsidian", ".trash", ".grok", ".claude", "template", "attachments"}
ORPHAN_EXEMPT_DIRS = {"daily", "Inbox", "Memo"}
LINK_RE = re.compile(r"(!?)\[\[([^\]\|#^]+)(?:[#^][^\]\|]*)?(?:\|[^\]]*)?\]\]")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

DIST_MAX = 1.05      # これより遠い候補は出さない(弱い一致を握らせない)
TOP_PER_NOTE = 3     # 1孤立あたりの提案数
KNN = 40             # 近傍チャンクを何件見てからノート単位に畳むか


def strip_code(text: str) -> str:
    out, fenced = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append(re.sub(r"`[^`\n]*`", " ", line))
    return "\n".join(out)


def parse_note(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip("\"'")
    return fm, text[end + 4:]


class Note:
    def __init__(self, path: Path):
        self.rel = str(path.relative_to(VAULT))
        self.stem = path.stem
        self.folder = self.rel.split("/")[0] if "/" in self.rel else ""
        self.fm, body = parse_note(path.read_text(encoding="utf-8", errors="replace"))
        self.links = {t.strip() for _, t in LINK_RE.findall(strip_code(body)) if t.strip()}
        self.structural = str(self.fm.get("ai", "")).lower() == "false"


def embed(text: str) -> list[float]:
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps({"model": EMBED_MODEL,
                         "input": f"task: search result | query: {text}"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read())["embeddings"][0]


def serialize(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def collect_notes() -> list[Note]:
    notes = []
    for p in sorted(VAULT.rglob("*.md")):
        if any(part in SKIP_DIRS for part in p.relative_to(VAULT).parts):
            continue
        notes.append(Note(p))
    return notes


def find_orphans(notes: list[Note]) -> list[Note]:
    inbound: set[str] = set()
    for n in notes:
        inbound |= n.links
    orphans = []
    for n in notes:
        if n.structural or not n.folder or n.folder in ORPHAN_EXEMPT_DIRS:
            continue
        if n.stem not in inbound:
            orphans.append(n)
    return orphans


def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def suggest_for(db: sqlite3.Connection, note: Note) -> list[tuple[str, float]]:
    """note の本文に近い他ノートを (stem, 距離) で返す。"""
    row = db.execute(
        "SELECT group_concat(content, ' ') FROM chunks WHERE path = ?", (note.rel,)
    ).fetchone()
    if not row or not row[0]:
        return []  # 索引に載っていない(未 index の新規ノート等)

    qvec = serialize(embed(row[0][:2000]))
    rows = db.execute(
        """
        SELECT c.path, c.title, min(v.distance) AS d
        FROM chunks_vec v JOIN chunks c ON c.id = v.rowid
        WHERE v.embedding MATCH ? AND k = ?
        GROUP BY c.path ORDER BY d
        """,
        (qvec, KNN),
    ).fetchall()

    out = []
    for path, title, dist in rows:
        stem = Path(path).stem
        if path == note.rel or stem in note.links:
            continue  # 自分自身と既にリンク済みは除く
        if dist > DIST_MAX:
            continue
        out.append((stem, dist))
        if len(out) >= TOP_PER_NOTE:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="対象の孤立ノート数の上限")
    ap.add_argument(
        "--file", type=Path,
        help="このノート1件だけ提案する(索引済み前提。PostToolUse フック用、孤立判定はしない)",
    )
    args = ap.parse_args()

    if not DB_PATH.exists():
        print("vault.db が無い。先に vault_search.py index を実行する。", file=sys.stderr)
        sys.exit(1)

    if args.file:
        try:
            note = Note(args.file.resolve())
        except ValueError:
            print("Vault 外のファイル。", file=sys.stderr)
            sys.exit(1)
        db = open_db()
        cands = suggest_for(db, note)
        if cands:
            links = "  ".join(f"[[{s}]] ({d:.2f})" for s, d in cands)
            print(f"{note.rel}\n    → {links}")
        else:
            print(f"{note.rel}\n    → (近い候補なし。索引直後で未反映の可能性あり)")
        return

    notes = collect_notes()
    orphans = find_orphans(notes)
    if args.limit:
        orphans = orphans[:args.limit]

    if not orphans:
        print("孤立ノートなし。")
        return

    db = open_db()
    any_hit = False
    for n in orphans:
        cands = suggest_for(db, n)
        if cands:
            any_hit = True
            links = "  ".join(f"[[{s}]] ({d:.2f})" for s, d in cands)
            print(f"{n.rel}\n    → {links}")
        else:
            print(f"{n.rel}\n    → (近い候補なし)")
    if not any_hit:
        print("\n近い候補が見つからなかった。索引が古いなら vault_search.py index を先に。")


if __name__ == "__main__":
    main()
