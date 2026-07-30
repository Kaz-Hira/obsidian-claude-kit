#!/usr/bin/env python3
"""Inbox のタグ無しノートに、既存のタグ体系から最適な1つを付ける。

gpt-oss:20b(ローカル)に本文を渡し、**Vault に実在するタグの集合の中から**選ばせる。
自由生成させると英語や新語が混じり、Auto Note Mover(タグ→フォルダの契約)が拾えないため、
候補を列挙して「この中から選べ、なければ NONE」と制約する。

既定は提案のみ(dry-run)。間違ったタグは即フォルダ移動を招くので、
中身を見て納得してから `--apply` で書き込む。

  vault-autotag.py            # 提案だけ表示
  vault-autotag.py --apply    # frontmatter に書き込む(Auto Note Mover が移動)
"""

from __future__ import annotations  # 3.9 の /usr/bin/python3 で実行されても
# `X | None` 等の PEP 604 が def 実行時に評価されないようにする(2026-07-26)。
# これが無いと py_compile は通るのに実行時 TypeError で即死する。

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
INBOX = VAULT / "Inbox"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gpt-oss:20b"


def collect_tags() -> list[str]:
    """Vault 全体の frontmatter から階層タグ(category/sub)を集める。"""
    tags = set()
    pat = re.compile(r"^\s*-\s*(study|memo|blog|ref|sys)/(\S+?)\s*$")
    for p in VAULT.rglob("*.md"):
        if p.relative_to(VAULT).parts[0] in {".trash", ".obsidian", ".claude", "template"}:
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:800]
        except OSError:
            continue
        for line in head.splitlines():
            m = pat.match(line)
            if m:
                tags.add(f"{m.group(1)}/{m.group(2)}")
    return sorted(tags)


def note_body(text: str) -> str:
    """frontmatter を除いた本文。"""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4 :].strip()
    return text.strip()


def has_tag(text: str) -> bool:
    """frontmatter の tags に既に階層タグがあるか。"""
    m = re.search(r"^tags:\s*\n((?:\s*-\s*\S+\n)+)", text, re.MULTILINE)
    return bool(m and re.search(r"(study|memo|blog|ref|sys)/", m.group(1)))


def ask_model(body: str, tags: list[str]) -> str:
    tag_list = "\n".join(f"- {t}" for t in tags)
    prompt = (
        "あなたは Obsidian ノートの分類器です。次のノート本文に最も合うタグを、"
        "下の候補リストから**厳密に1つだけ**選び、そのタグ文字列のみを出力してください。"
        "どれにも当てはまらなければ NONE とだけ出力。説明・記号・引用符は一切不要。\n\n"
        f"# 候補タグ\n{tag_list}\n\n# ノート本文\n{body[:1500]}\n\n# 出力(タグ1つ or NONE)\n"
    )
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                         "options": {"temperature": 0}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as res:
        return json.loads(res.read())["response"].strip()


def apply_tag(path: Path, text: str, tag: str):
    """frontmatter の空の `tags:` 行にタグを1つ挿入する。"""
    new = re.sub(r"^tags:\s*$", f"tags:\n  - {tag}", text, count=1, flags=re.MULTILINE)
    path.write_text(new, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="実際に frontmatter へ書き込む")
    ap.add_argument("--limit", type=int, default=0, help="処理する Inbox ノート数の上限")
    args = ap.parse_args()

    tags = collect_tags()
    if not tags:
        print("既存タグが集まらなかった。中止。", file=sys.stderr)
        sys.exit(1)

    targets = []
    # クイックキャプチャ.md は断片を溜める常設バッファ。中身は混在するので、
    # 個々の断片が実ノートに切り出されるまで、バッファ自体は仕分け対象にしない。
    BUFFER = "クイックキャプチャ.md"
    for p in sorted(INBOX.glob("*.md")):
        if p.name == BUFFER:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        body = note_body(text)
        if not body or has_tag(text):
            continue
        targets.append((p, text, body))

    if args.limit:
        targets = targets[:args.limit]

    if not targets:
        print("タグ付け対象の Inbox ノートはありません。")
        return

    allowed = set(tags)
    for p, text, body in targets:
        raw = ask_model(body, tags)
        tag = raw.splitlines()[0].strip().lstrip("#") if raw else "NONE"
        preview = re.sub(r"\s+", " ", body)[:50]

        if tag in allowed:
            mark = "→ 書き込み" if args.apply else "→ 提案"
            print(f"[{tag}] {mark}  {p.name}  「{preview}」")
            if args.apply:
                apply_tag(p, text, tag)
        else:
            print(f"[NONE/対象外: {tag!r}] スキップ  {p.name}  「{preview}」")

    if not args.apply:
        print("\n提案のみ。書き込むなら --apply。書き込むと Auto Note Mover が該当フォルダへ移動する。")


if __name__ == "__main__":
    main()
