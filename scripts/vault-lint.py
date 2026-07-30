#!/usr/bin/env python3
"""Obsidian Vault の健全性チェック。

このVault固有の規約を検査する:
  - uniquenote.md の frontmatter スキーマ (title/uid/created/updated/tags)
  - auto-note-mover の契約 (Memo/→#memo, Blog/→#blog, Study/→#study)
  - AIコンテキスト.md の `ai:` 規約 (構造ページは ai: false)
  - リンク切れ・孤立ノート・未参照の添付

`--file <path>` で単一ファイルだけの高速チェック(PostToolUse フック用)。
読み取り専用。ファイルは一切変更しない。依存パッケージなし。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")

# 走査対象から外すディレクトリ
SKIP_DIRS = {".obsidian", ".trash", ".grok", "template"}

# 孤立チェックの対象外。日記は連番で辿るもの、Inbox は未整理の置き場、
# Memo は走り書き(恒久的な資料は Reference/ にあり、そちらは孤立を許さない)
ORPHAN_EXEMPT_DIRS = {"daily", "Inbox", "Memo"}

# 分量チェックの対象外。走り書きと日記は短くて当たり前
THIN_EXEMPT_DIRS = {"daily", "Memo", "Inbox"}

# リンク切れチェックの対象外。periodic-notes のテンプレートは
# [[昨日]] [[明日]] や月内の全日を機械的に張るため、未作成の日を指すのが正常
LINK_EXEMPT_DIRS = {"daily"}

# auto-note-mover の folder_tag_pattern と対応させる
TAG_CONTRACT = {"Reference": "ref", "System": "sys", "Memo": "memo", "Blog": "blog", "Study": "study"}

# uniquenote.md が定める本文ノートの必須キー
REQUIRED_KEYS = ("title", "uid", "created", "updated", "tags")

FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# [[Target]] / [[Target|alias]] / [[Target#heading]] / ![[embed]]
LINK_RE = re.compile(r"(!?)\[\[([^\]\|#^]+)(?:[#^][^\]\|]*)?(?:\|[^\]]*)?\]\]")
# ![alt](attachments/foo.png) 形式の添付参照
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# 見出し記号・強調・リスト記号など、分量として数えない装飾
DECORATION_RE = re.compile(r"[#*`>\-\[\]!|_~\s]")


def strip_code(text: str) -> str:
    """コードブロックとインラインコードを除去する。

    このVaultのノートは Obsidian の記法そのものを解説しているため、
    `[[リンク]]` のようなコード内の記述をリンクとして数えると誤検出になる。
    """
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(INLINE_CODE_RE.sub("", line))
    return "\n".join(out)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """先頭の YAML frontmatter を、必要な範囲だけ読む。(値, 本文) を返す。

    スカラーとブロックリスト (`- item`) のみ扱う。このVaultの実際の書式に足りる。
    """
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
                # `tags:` は値なしのスカラーとして None が入っている。
                # 続くブロックリストを読むにはここでリストへ昇格させる
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
        self.fm, self.body = parse_frontmatter(text)
        clean = strip_code(self.body)
        self.clean = clean
        self.links: set[str] = set()
        self.embeds: set[str] = set()
        for bang, target in LINK_RE.findall(clean):
            t = target.strip()
            if t:
                (self.embeds if bang else self.links).add(t)
        # ![alt](attachments/foo.png) も添付の参照として数える
        self.md_images = {m.split("/")[-1] for m in MD_IMAGE_RE.findall(clean)}

    @property
    def ai(self) -> str | None:
        v = self.fm.get("ai")
        return v.lower() if isinstance(v, str) else None

    @property
    def is_structural(self) -> bool:
        """構造ページ(ダッシュボード・カンバン・MOC)。ai: false が付いているもの。"""
        return self.ai == "false"

    @property
    def link_ignore(self) -> set[str]:
        """`lint-ignore-links:` に列挙されたリンク先を、リンク切れ判定から除外する。

        Obsidian の記法を解説するノートでは `[[ノートリンク]]` のような【例示】が
        本物のリンクとして数えられ、恒久的な誤検出になる。Dataview のデモ表に
        架空の書名を並べた場合も同じ。これらは「直すべきリンク切れ」ではないので、
        除外の理由をノート自身の frontmatter に残せるようにする。

        インラインコード化(`` `[[..]]` ``)でも消せるが、公開済み記事の見た目が
        変わってしまうため、frontmatter で宣言する方式を採る。

        重大カウントが常に 3 のままだと、SessionStart ダッシュボードの「重大 n」が
        何のシグナルにもならない。0 を保てることが指標として意味を持つ条件。
        """
        v = self.fm.get("lint-ignore-links")
        if isinstance(v, list):
            return {str(x).strip() for x in v if str(x).strip()}
        if isinstance(v, str) and v.strip():
            return {v.strip()}
        return set()

    @property
    def tags(self) -> list[str]:
        t = self.fm.get("tags")
        if isinstance(t, list):
            return t
        if isinstance(t, str) and t:
            return [t]
        return []

    def content_chars(self) -> int:
        """本文の実質的な分量。日本語は語の区切りに空白を使わないので文字数で測る。"""
        return len(DECORATION_RE.sub("", self.clean))


def collect() -> tuple[list[Note], set[str], set[str]]:
    """ノート・添付・その他の Vault 内ファイルを集める。

    `others` は .base や .canvas のような添付でない非 .md ファイル。
    Obsidian は [[2026期末対策.base]] のように拡張子付きでリンクできるので、
    リンク解決の対象に含める。未参照チェックの対象にはしない(添付ではない)。
    """
    notes, attachments, others = [], set(), set()
    for p in sorted(VAULT.rglob("*")):
        rel = p.relative_to(VAULT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if not p.is_file():
            continue
        if p.suffix == ".md":
            notes.append(Note(p))
        elif p.parts[-2:-1] == ("attachments",):
            attachments.add(p.name)
        else:
            # ベース名でもパスでも解決できるようにする
            others |= {p.name, str(rel), str(rel.with_suffix(""))}
    return notes, attachments, others


def build_index(notes: list[Note]) -> dict[str, Note]:
    """Obsidian のリンク解決を近似する: ベース名 + エイリアス + Vault相対パス。"""
    idx: dict[str, Note] = {}
    for n in notes:
        idx.setdefault(n.stem, n)
        idx.setdefault(str(n.rel.with_suffix("")), n)
        a = n.fm.get("aliases")
        for alias in a if isinstance(a, list) else []:
            idx.setdefault(alias, n)
    return idx


def lint_file(path: Path) -> int:
    """編集された1ファイルだけを検査する(PostToolUse フック用)。

    Vault 全体を読むのはリンク解決の索引を作るためで、報告するのはこのファイルの分だけ。
    孤立・分量・未参照添付のような Vault 全体の状態に依存する指摘は、
    編集のたびに出しても行動につながらないので含めない。
    違反が無ければ何も出力しない。終了コードは常に 0(フックは編集を止めない)。
    """
    try:
        rel = path.resolve().relative_to(VAULT)
    except ValueError:
        return 0
    path = VAULT / rel
    if path.suffix != ".md" or not path.is_file():
        return 0
    if any(part in SKIP_DIRS for part in rel.parts):
        return 0

    notes, attachments, others = collect()
    idx = build_index(notes)
    note = next((n for n in notes if n.path == path), None)
    if note is None:
        return 0

    issues: list[str] = []

    # 1. リンク切れ(lint-ignore-links で宣言されたものは除く)
    if note.folder not in LINK_EXEMPT_DIRS:
        ignore = note.link_ignore
        for target in note.links:
            if (target not in idx and target not in attachments
                    and target not in others and target not in ignore):
                issues.append(f"重大: リンク切れ  [[{target}]]")
        for target in note.embeds:
            base = target.split("/")[-1]
            if (base not in attachments and target not in idx
                    and target not in others and target not in ignore):
                issues.append(f"重大: 埋め込み切れ  ![[{target}]]")

    # 2. auto-note-mover のタグ契約
    want = TAG_CONTRACT.get(note.folder)
    if want and not note.is_structural:
        norm = [t.lstrip("#") for t in note.tags]
        if not any(t == want or t.startswith(want + "/") for t in norm):
            issues.append(f"重大: タグ契約違反  #{want} 系のタグが無く、auto-note-mover が再配置できない")
        for t in note.tags:
            if t.startswith("#"):
                issues.append(f"警告: タグ表記ゆれ  `{t}` は他ノートに倣い `{t.lstrip('#')}` に揃える")

    # 3. frontmatter の欠落
    if not note.is_structural and note.folder not in ("daily", "MOC"):
        missing = [k for k in REQUIRED_KEYS if not note.fm.get(k)]
        if missing:
            issues.append(f"警告: frontmatter 欠落  {', '.join(missing)}")

    # 4. ai: 規約
    if note.folder == "MOC" and note.ai != "false":
        got = f"ai: {note.ai}" if note.ai else "ai: 未設定"
        issues.append(f"警告: MOC は ai: false のはずが {got}")

    for line in issues:
        print(f"{rel}  {line}")
    return 0


def analyze() -> dict:
    """Vault を全走査して 重大/警告/提案 の3バケットを返す(表示はしない)。

    表示から分離してあるのは、SessionStart ダッシュボード(vault-status.py)が
    同じ判定を再実装せずに済むようにするため。数字の定義が `/lint` と
    ダッシュボードでズレると、どちらの数字を信じてよいか分からなくなる。
    vault-status.py は importlib でこのモジュールを読んで analyze() を呼ぶ。

    返り値のキー: notes, attachments, critical, warn, suggest, orphan
    """
    notes, attachments, others = collect()
    idx = build_index(notes)

    critical: list[str] = []
    warn: list[str] = []
    suggest: list[str] = []

    # --- 1. リンク切れ -------------------------------------------------
    inbound: dict[str, set[str]] = defaultdict(set)
    used_attachments: set[str] = set()
    for n in notes:
        # periodic-notes が機械的に張る隣接リンクは、未作成の日を指すのが正常
        report_dead = n.folder not in LINK_EXEMPT_DIRS
        used_attachments |= n.md_images & attachments
        ignore = n.link_ignore
        for target in n.links:
            if target in idx:
                if idx[target] is not n:
                    inbound[idx[target].stem].add(str(n.rel))
            elif target in ignore:
                pass  # 記法の例示・架空の書名。frontmatter で宣言済み
            elif target not in attachments and target not in others and report_dead:
                critical.append(f"リンク切れ  {n.rel}  →  [[{target}]]")
        for target in n.embeds:
            base = target.split("/")[-1]
            if base in attachments:
                used_attachments.add(base)
            elif target in idx:
                inbound[idx[target].stem].add(str(n.rel))
            elif target in others or target in ignore:
                pass
            elif report_dead:
                critical.append(f"埋め込み切れ  {n.rel}  →  ![[{target}]]")

    # --- 2. auto-note-mover のタグ契約 ---------------------------------
    # 契約を破ったノートは、タグを付け直しても自動移動されない。
    # プラグイン側の判定は正規表現 `(^|#)memo($|/)` なので、先頭の `#` は許容される
    for n in notes:
        want = TAG_CONTRACT.get(n.folder)
        if not want or n.is_structural:
            continue
        norm = [t.lstrip("#") for t in n.tags]
        if not any(t == want or t.startswith(want + "/") for t in norm):
            critical.append(
                f"タグ契約違反  {n.rel}  →  #{want} 系のタグが無く、auto-note-mover が再配置できない"
            )
        # 動作はするが、Vault 内の他ノートは `#` を付けない書き方で統一されている
        for t in n.tags:
            if t.startswith("#"):
                warn.append(f"タグ表記ゆれ  {n.rel}  →  `{t}` は他ノートに倣い `{t.lstrip('#')}` に揃える")

    # --- 3. frontmatter の欠落 -----------------------------------------
    for n in notes:
        if n.is_structural or n.folder in ("daily", "MOC"):
            continue
        missing = [k for k in REQUIRED_KEYS if not n.fm.get(k)]
        if missing:
            warn.append(f"frontmatter 欠落  {n.rel}  →  {', '.join(missing)}")

    # --- 4. ai: 規約の一貫性 --------------------------------------------
    # AIコンテキスト.md:「構造・ダッシュボード・カンバン・MOC には ai: false」
    for n in notes:
        if n.folder == "MOC" and n.ai != "false":
            got = f"ai: {n.ai}" if n.ai else "ai: 未設定"
            warn.append(f"ai 規約  {n.rel}  →  MOC は ai: false のはずが {got}")

    # --- 5. 孤立ノート ---------------------------------------------------
    orphan: list[str] = []
    for n in notes:
        if n.is_structural or n.folder in ORPHAN_EXEMPT_DIRS or not n.folder:
            continue
        if not inbound.get(n.stem):
            orphan.append(str(n.rel))
            suggest.append(f"孤立  {n.rel}  →  どこからもリンクされていない")

    # --- 6. 中身の薄いノート ---------------------------------------------
    for n in notes:
        if n.is_structural or n.folder in THIN_EXEMPT_DIRS:
            continue
        chars = n.content_chars()
        if chars < 120:
            suggest.append(f"内容が薄い  {n.rel}  →  本文 {chars} 文字")

    # --- 7. 未参照の添付 -------------------------------------------------
    for a in sorted(attachments - used_attachments):
        suggest.append(f"未参照の添付  attachments/{a}")

    return {
        "notes": len(notes),
        "attachments": len(attachments),
        "critical": critical,
        "warn": warn,
        "suggest": suggest,
        "orphan": orphan,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Obsidian Vault 健全性チェック")
    ap.add_argument("--only", choices=["critical", "warn", "suggest"], help="指定の重大度だけ表示")
    ap.add_argument("--file", type=Path, help="このファイルだけ検査する(PostToolUse フック用)")
    args = ap.parse_args()

    if not VAULT.is_dir():
        print(f"Vault が見つからない: {VAULT}", file=sys.stderr)
        return 1

    if args.file:
        return lint_file(args.file)

    r = analyze()
    critical, warn, suggest = r["critical"], r["warn"], r["suggest"]

    # --- 出力 -------------------------------------------------------------
    buckets = [("重大", critical), ("警告", warn), ("提案", suggest)]
    keep = {"critical": "重大", "warn": "警告", "suggest": "提案"}.get(args.only or "")

    print(f"Vault: {VAULT}")
    print(f"ノート {r['notes']} 件 / 添付 {r['attachments']} 件")
    print(f"重大 {len(critical)} / 警告 {len(warn)} / 提案 {len(suggest)}")

    for label, items in buckets:
        if keep and label != keep:
            continue
        print(f"\n{'─' * 60}\n{label} ({len(items)})\n{'─' * 60}")
        if not items:
            print("  なし")
        for line in items:
            print(f"  {line}")

    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
