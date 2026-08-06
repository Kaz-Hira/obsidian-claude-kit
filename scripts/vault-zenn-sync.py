#!/usr/bin/env python3
"""Vault の Blog/ を正本として Zenn のコンテンツリポジトリへ記事を同期する。

Vault のノートは Obsidian 用の frontmatter(uid / created / updated / tags)を持ち、
Zenn は Zenn 用の frontmatter(emoji / type / topics / published)を要求する。
両方を1枚のノートに同居させ、このスクリプトが Zenn 側の形に変換して書き出す。

Vault 側のノートに次の `zenn:` ブロックがあるものだけが対象になる:

    ---
    title: 記事タイトル
    uid: "20260805090800"
    tags:
      - blog/obsidian
    status: draft
    zenn:
      slug: obsidian-ai-note-consumption   # 必須。記事URLになる
      emoji: "🗂️"
      type: tech                            # tech | idea
      topics: [obsidian, claudecode]        # 最大5件
      published: false
    ---

使い方:
    vault-zenn-sync.py            # 同期する
    vault-zenn-sync.py --check    # 差分があるかだけ見る(書き込まない。CI向け)

`published: true` のものは公開されるため、同期時に明示的に警告する。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
ZENN = _envpath("ZENN", "~/dev/zenn-content")

BLOG_DIR = VAULT / "Blog"
ARTICLES_DIR = ZENN / "articles"

# Zenn の制約
SLUG_RE = re.compile(r"^[a-z0-9_-]{12,50}$")
VALID_TYPES = {"tech", "idea"}
MAX_TOPICS = 5

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
LEADING_H1_RE = re.compile(r"\A\s*#\s+.*?\n+", re.DOTALL)


class SkipNote(Exception):
    """同期対象外(エラーではない)。"""


def split_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SkipNote("frontmatter なし")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise SkipNote("frontmatter が辞書でない")
    return meta, m.group(2)


def validate(zenn_meta: dict, title: str, path: Path) -> list[str]:
    """Zenn に弾かれる前にこちらで落とす。"""
    errors = []
    slug = zenn_meta.get("slug")
    if not slug:
        errors.append("zenn.slug が無い(記事URLになるので必須)")
    elif not SLUG_RE.match(str(slug)):
        errors.append(f"zenn.slug '{slug}' が不正(英小文字・数字・_・- で12〜50文字)")

    if not title:
        errors.append("title が空")

    emoji = zenn_meta.get("emoji")
    if not emoji:
        errors.append("zenn.emoji が無い")

    typ = zenn_meta.get("type", "tech")
    if typ not in VALID_TYPES:
        errors.append(f"zenn.type '{typ}' が不正(tech か idea)")

    topics = zenn_meta.get("topics") or []
    if not isinstance(topics, list):
        errors.append("zenn.topics がリストでない")
    elif len(topics) > MAX_TOPICS:
        errors.append(f"zenn.topics が {len(topics)} 件(最大 {MAX_TOPICS})")

    return errors


def build_zenn_frontmatter(zenn_meta: dict, title: str) -> str:
    """Zenn が読む frontmatter だけを組み立てる。Obsidian 用の項目は落とす。"""
    topics = zenn_meta.get("topics") or []
    # YAML の二重引用符スカラは JSON の文字列とエスケープ規則が一致するので、
    # json.dumps に任せると引用符やバックスラッシュを含むタイトルでも壊れない。
    lines = [
        "---",
        f"title: {json.dumps(str(title), ensure_ascii=False)}",
        f'emoji: "{zenn_meta.get("emoji", "📝")}"',
        f'type: "{zenn_meta.get("type", "tech")}"',
        "topics: [" + ", ".join(f'"{t}"' for t in topics) + "]",
        f'published: {"true" if zenn_meta.get("published") else "false"}',
    ]
    published_at = zenn_meta.get("published_at")
    if published_at:
        lines.append(f'published_at: "{published_at}"')
    lines.append("---")
    return "\n".join(lines) + "\n"


def convert(path: Path) -> tuple[str, str, list[str]] | None:
    """1枚のノートを Zenn 記事の中身に変換する。(slug, 本文, 警告) を返す。"""
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    zenn_meta = meta.get("zenn")
    if not isinstance(zenn_meta, dict):
        raise SkipNote("zenn ブロックなし")

    title = zenn_meta.get("title") or meta.get("title") or ""
    errors = validate(zenn_meta, title, path)
    if errors:
        raise ValueError("; ".join(errors))

    warnings: list[str] = []

    # Zenn は frontmatter の title を見出しとして描画するので、本文先頭の H1 は重複する。
    body_stripped = LEADING_H1_RE.sub("", body, count=1)
    if body_stripped != body:
        body = body_stripped

    # Obsidian の [[wikilink]] は Zenn では素通しされ、ただの記号として表示される。
    links = WIKILINK_RE.findall(body)
    if links:
        warnings.append(
            f"[[wikilink]] が {len(links)} 件残っている(Zenn では展開されない): "
            + ", ".join(links[:3])
            + ("…" if len(links) > 3 else "")
        )

    if zenn_meta.get("published"):
        warnings.append("published: true — push すると公開される")

    content = build_zenn_frontmatter(zenn_meta, title) + "\n" + body.lstrip("\n")
    return str(zenn_meta["slug"]), content, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="Vault の Blog/ を Zenn へ同期する")
    ap.add_argument("--check", action="store_true", help="書き込まず差分の有無だけ報告する")
    args = ap.parse_args()

    if not BLOG_DIR.is_dir():
        print(f"Blog ディレクトリが無い: {BLOG_DIR}", file=sys.stderr)
        return 1
    if not ARTICLES_DIR.is_dir():
        print(f"Zenn の articles が無い: {ARTICLES_DIR}", file=sys.stderr)
        return 1

    changed, unchanged, skipped, failed = [], [], [], []
    all_warnings: list[str] = []

    for path in sorted(BLOG_DIR.glob("*.md")):
        try:
            result = convert(path)
        except SkipNote:
            skipped.append(path.name)
            continue
        except (ValueError, yaml.YAMLError) as e:
            failed.append(f"{path.name}: {e}")
            continue
        if result is None:
            skipped.append(path.name)
            continue

        slug, content, warnings = result
        all_warnings += [f"{path.name}: {w}" for w in warnings]
        out = ARTICLES_DIR / f"{slug}.md"

        if out.exists() and out.read_text(encoding="utf-8") == content:
            unchanged.append(slug)
            continue

        changed.append(slug)
        if not args.check:
            out.write_text(content, encoding="utf-8")

    verb = "差分あり" if args.check else "同期"
    if changed:
        print(f"{verb}: {len(changed)} 件")
        for s in changed:
            print(f"  - {s}")
    if unchanged:
        print(f"変更なし: {len(unchanged)} 件")
    if skipped:
        print(f"対象外(zenn ブロックなし): {len(skipped)} 件")

    for w in all_warnings:
        print(f"⚠️  {w}")

    if failed:
        print(f"\n❌ 変換できなかったノート {len(failed)} 件:", file=sys.stderr)
        for f in failed:
            print(f"  - {f}", file=sys.stderr)
        return 1

    # --check は「差分があれば非ゼロ」で終わる(CI で使えるように)
    if args.check and changed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
