#!/usr/bin/env python3
"""MOC(Map of Content)のメンバーシップ網羅性チェック。

`/lint` はリンク切れ・frontmatter欠落・「vault全体でinboundが0件」の孤立検知は
行うが、「MOC/*.md のどれからもリンクされていない」かどうかは見ていない
(他の通常ノートからリンクされていれば孤立とは判定されないため)。

このスクリプトは Reference/System/Study(専用MOCを持つ3フォルダ)のノートに
絞り、MOC/*.md 全体からのリンク集合と突き合わせて「タグはあるがどのMOCにも
載っていないノート」を洗い出す。リンク切れ自体は /lint が既に検出するため
ここでは扱わない。読み取り専用。ファイルは一切変更しない。依存パッケージなし。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
SKIP_DIRS = {".obsidian", ".trash", ".grok", "template"}

# 専用MOCを持つフォルダのみ対象。Memo/Blog は MOC.md 自身の Dataview
# クエリ(「まだどのMOCにも繋がっていないノート」)で既にカバーされている
TARGET_FOLDERS = {"Reference", "System", "Study"}

FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
LINK_RE = re.compile(r"(!?)\[\[([^\]\|#^]+)(?:[#^][^\]\|]*)?(?:\|[^\]]*)?\]\]")


def strip_code(text: str) -> str:
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(INLINE_CODE_RE.sub("", line))
    return "\n".join(out)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    fm: dict = {}
    key = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t", "-")):
            item = line.strip().lstrip("-").strip()
            if key and item:
                if not isinstance(fm.get(key), list):
                    fm[key] = []
                fm[key].append(item.strip("\"'"))
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        fm[key] = val.strip("\"'") if val else None
    return fm, body


class Note:
    def __init__(self, path: Path):
        self.path = path
        self.rel = path.relative_to(VAULT)
        self.stem = path.stem
        self.folder = self.rel.parts[0] if len(self.rel.parts) > 1 else ""
        text = path.read_text(encoding="utf-8", errors="replace")
        self.fm, body = parse_frontmatter(text)
        clean = strip_code(body)
        self.links: set[str] = set()
        for bang, target in LINK_RE.findall(clean):
            t = target.strip()
            if t and not bang:
                self.links.add(t)

    @property
    def ai(self) -> str | None:
        v = self.fm.get("ai")
        return v.lower() if isinstance(v, str) else None

    @property
    def is_structural(self) -> bool:
        return self.ai == "false"

    @property
    def tags(self) -> list[str]:
        t = self.fm.get("tags")
        if isinstance(t, list):
            return t
        if isinstance(t, str) and t:
            return [t]
        return []


def collect() -> list[Note]:
    notes = []
    for p in sorted(VAULT.rglob("*.md")):
        if any(part in SKIP_DIRS for part in p.relative_to(VAULT).parts):
            continue
        notes.append(Note(p))
    return notes


def build_index(notes: list[Note]) -> dict[str, Note]:
    idx: dict[str, Note] = {}
    for n in notes:
        idx.setdefault(n.stem, n)
        idx.setdefault(str(n.rel.with_suffix("")), n)
        a = n.fm.get("aliases")
        for alias in a if isinstance(a, list) else []:
            idx.setdefault(alias, n)
    return idx


def analyze(folder: str | None = None) -> dict:
    """MOC 未収録ノートを返す(表示はしない)。

    表示から分離してあるのは、週次ダイジェスト(vault-weekly.py)が同じ判定を
    再実装せずに済むようにするため。vault-lint.py の analyze() と同じ方針。
    これで moc-audit スキルは「起動0回」から「週次で件数を目にする」に変わり、
    深掘りしたくなったときの入口として生き続ける。

    返り値: {"targets": int, "mocs": int, "uncovered": [Note], "by_folder": {str: int}}
    """
    notes = collect()
    idx = build_index(notes)
    folders = {folder} if folder else TARGET_FOLDERS

    mocs = [n for n in notes if n.folder == "MOC"]
    moc_linked: set[str] = set()
    for m in mocs:
        for target in m.links:
            resolved = idx.get(target)
            moc_linked.add(resolved.stem if resolved else target)

    targets = [
        n
        for n in notes
        if n.folder in folders and not n.is_structural and n.folder != "MOC"
    ]

    uncovered = [n for n in targets if n.stem not in moc_linked]
    uncovered.sort(key=lambda n: str(n.rel))

    by_folder: dict[str, int] = {}
    for n in uncovered:
        by_folder[n.folder] = by_folder.get(n.folder, 0) + 1

    return {"targets": len(targets), "mocs": len(mocs),
            "uncovered": uncovered, "by_folder": by_folder,
            "folders": sorted(folders)}


def main() -> int:
    ap = argparse.ArgumentParser(description="MOCメンバーシップの網羅性チェック")
    ap.add_argument(
        "--folder",
        choices=sorted(TARGET_FOLDERS),
        help="このフォルダだけ検査する(省略時は Reference/System/Study 全部)",
    )
    args = ap.parse_args()

    if not VAULT.is_dir():
        print(f"Vault が見つからない: {VAULT}", file=sys.stderr)
        return 1

    r = analyze(args.folder)
    targets, mocs, uncovered, folders = (
        r["targets"], r["mocs"], r["uncovered"], r["folders"])

    print(f"Vault: {VAULT}")
    print(f"対象ノート {targets} 件({'/'.join(folders)}) / MOC {mocs} 件")
    print(f"MOC未収録 {len(uncovered)} 件(リンク切れ自体は /lint を参照)")
    for n in uncovered:
        tags = ", ".join(n.tags) if n.tags else "(タグなし)"
        print(f"  {n.rel}  tags: {tags}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
