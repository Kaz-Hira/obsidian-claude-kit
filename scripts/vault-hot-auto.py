#!/usr/bin/env python3
"""hot.md の「最近の変更」節を、git から機械的に再生成する。

hot.md は SessionStart で毎回注入される唯一の自動文脈なのに、更新は手動の
`/hot` 頼みで、実行3回で止まっていた(2026-07-20 で6日間停止し、既に解決済み
または誤検出だった TODO を毎セッション配り続けていた)。

そこで hot.md を2層に分ける:

  機械が書ける  … 「最近の変更」= git log + git status から完全に導出できる
  Claude が書く … 「直近の事実」「進行中のスレッド」= セッションの意味と中断点

このスクリプトは前者だけを担当し、`<!-- hot:auto:start -->` 〜 `end` の間を
冪等に置換する。マーカーで囲んだブロックだけを差し替える方式は
vault-weekly.py で既に実績があるイディオム。

frontmatter の `updated:` は【触らない】。あれは手動部の鮮度を表す値で、
機械更新で見かけ上新しくなると、ダッシュボードの「hot.md が古い」判定が
機能しなくなる。機械側の時刻は `auto-updated:` に別途持つ。

  vault-hot-auto.py           # 更新する(変化が無ければ書かない)
  vault-hot-auto.py --check   # 差分が出るかだけ見る(書かない)
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
DOTFILES = _envpath("DOTFILES", "~/dotfiles")
HOT = VAULT / "hot.md"
START = "<!-- hot:auto:start -->"
END = "<!-- hot:auto:end -->"
MAX_ITEMS = 8
SINCE = "3 days ago"


def sh(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=15, check=False)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        # git が無い/タイムアウト。ここは hot.md の付録なので空で続行する
        return ""


def recent_commits(repo: Path, label: str) -> list[str]:
    out = sh(["git", "log", f"--since={SINCE}", "--pretty=format:%h\t%s"], repo)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return []
    rows = [f"- **{label}** 直近{len(lines)}コミット"]
    for ln in lines[:MAX_ITEMS]:
        h, _, subj = ln.partition("\t")
        rows.append(f"    - `{h}` {subj}")
    if len(lines) > MAX_ITEMS:
        rows.append(f"    - (他 {len(lines) - MAX_ITEMS} 件)")
    return rows


def uncommitted(repo: Path, label: str) -> list[str]:
    out = sh(["git", "-c", "core.quotepath=false", "status", "--porcelain"], repo)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return []
    rows = [f"- **{label} 未コミット {len(lines)}件**"]
    for ln in lines[:MAX_ITEMS]:
        rows.append(f"    - `{ln.strip()}`")
    if len(lines) > MAX_ITEMS:
        rows.append(f"    - (他 {len(lines) - MAX_ITEMS} 件)")
    return rows


def build_block() -> str:
    body: list[str] = []
    for repo, label in ((VAULT, "Vault"), (DOTFILES, "dotfiles")):
        body += recent_commits(repo, label)
    for repo, label in ((VAULT, "Vault"), (DOTFILES, "dotfiles")):
        body += uncommitted(repo, label)
    if not body:
        body = ["- (直近3日のコミットも未コミットの変更もない)"]
    return "\n".join([
        START,
        (
            f"<!-- vault-hot-auto.py が生成。手で書かない(次回の実行で上書きされる)。"
            f"最終 {dt.datetime.now():%Y-%m-%d %H:%M} -->"
        ),
        *body,
        END,
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="書かずに差分の有無だけ返す")
    args = ap.parse_args()

    if not HOT.exists():
        print(f"hot.md が無い: {HOT}", file=sys.stderr)
        return 1

    text = HOT.read_text(encoding="utf-8")
    block = build_block()

    if START in text and END in text:
        new = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block,
                     text, flags=re.DOTALL)
    else:
        # 初回だけ「## 最近の変更」節の中身をブロックで置き換える。
        # 節が見つからなければ何もしない(hot.md の構造を勝手に変えない)。
        m = re.search(r"^## 最近の変更\s*$", text, re.MULTILINE)
        if not m:
            print("「## 最近の変更」節が見つからないので何もしない", file=sys.stderr)
            return 0
        nxt = re.search(r"^## ", text[m.end():], re.MULTILINE)
        end_i = m.end() + (nxt.start() if nxt else len(text) - m.end())
        new = text[:m.end()] + "\n\n" + block + "\n\n" + text[end_i:]

    # auto-updated: は機械側の時刻。updated: (手動部の鮮度)は触らない。
    today = dt.date.today().isoformat()
    if re.search(r"^auto-updated:", new, re.MULTILINE):
        new = re.sub(r"^auto-updated:.*$", f"auto-updated: {today}", new,
                     count=1, flags=re.MULTILINE)
    else:
        new = re.sub(r"^(updated:.*)$", rf"\1\nauto-updated: {today}", new,
                     count=1, flags=re.MULTILINE)

    if new == text:
        return 0  # 変化なし: git の差分ノイズを増やさない
    if args.check:
        print("差分あり")
        return 0
    HOT.write_text(new, encoding="utf-8")
    print(f"hot.md の自動ブロックを更新しました: {HOT.relative_to(VAULT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
