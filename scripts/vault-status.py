#!/usr/bin/env python3
"""Vault の「今の溜まり具合」を1つの JSON にまとめる。

SessionStart ダッシュボード(vault-dashboard.sh)の唯一の入力。
判定そのものは持たず、数を数えるだけ。閾値と文面はフック側の責任。

  vault-status.py            # キャッシュがあれば使い、古ければ再生成
  vault-status.py --fresh    # キャッシュを無視して必ず再生成
  vault-status.py --refresh  # 再生成だけして終わる(バックグラウンド更新用)

lint の判定は vault-lint.py の analyze() を importlib で読んで使う。
シェルアウトして出力を再パースすると、数字の定義が2箇所に散って必ずズレる。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


SCRIPTS = _envpath("CLAUDE_DIR", "~/.claude") / "scripts"
VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
DOTFILES = _envpath("DOTFILES", "~/dotfiles")
CACHE = SCRIPTS / ".vault-status.json"
FAILLOG = SCRIPTS / ".vault-failures.jsonl"
HOT = VAULT / "hot.md"
CACHE_TTL = 600  # 10分

# ルート直下に置いてよいファイル。これ以外がルートにあれば「迷子」として数える。
#   hot.md          SessionStart で注入されるホットキャッシュ
#   Home.md         Vault の入口ダッシュボード
#   CLAUDE.md       project 規約(毎セッション自動ロード)
#   AIコンテキスト.md  LLM 常時知識の索引(CLAUDE.md が正本として指している)
ROOT_ALLOWED = {"hot.md", "Home.md", "README.md", "CLAUDE.md", "AIコンテキスト.md"}


def _load_lint():
    """vault-lint.py をモジュールとして読む(ハイフン入りなので import できない)。"""
    spec = importlib.util.spec_from_file_location("vault_lint", SCRIPTS / "vault-lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_count(repo: Path) -> int:
    """未コミット数。取得に失敗したときは 0 を返すが、黙って「変更なし」に化けないよう
    stderr に1行出す(ダッシュボードの数字が静かに嘘になるのを防ぐ)。
    """
    try:
        p = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  ! git 実行不可 {repo.name}: {type(e).__name__}", file=sys.stderr)
        return 0
    if p.returncode != 0:
        print(f"  ! git status 失敗 {repo.name}: rc={p.returncode}", file=sys.stderr)
        return 0
    return len([ln for ln in p.stdout.splitlines() if ln.strip()])


def _inbox() -> dict:
    d = VAULT / "Inbox"
    if not d.is_dir():
        return {"total": 0, "rss": 0, "oldest_days": 0}
    files = list(d.glob("*.md"))
    rss = [p for p in files if re.match(r"^\d{8}-RSS$", p.stem)]
    oldest = 0
    if files:
        oldest = int((time.time() - min(p.stat().st_mtime for p in files)) // 86400)
    return {"total": len(files), "rss": len(rss), "oldest_days": oldest}


def _jobs() -> dict:
    """連続失敗しているバックグラウンドジョブ。vault_bg_run が書いた jsonl を読む。"""
    if not FAILLOG.exists():
        return {}
    jobs: dict[str, dict] = {}
    for line in FAILLOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        j = rec.get("job")
        if not j:
            continue
        e = jobs.setdefault(j, {"consecutive_failures": 0, "last": "", "tail": ""})
        e["consecutive_failures"] += 1
        e["last"] = rec.get("ts", "")
        e["tail"] = rec.get("tail", "")
    return jobs


def _root_junk() -> list[str]:
    """ルート直下の規約外ファイル。Obsidian の UI が作る .base/.canvas の迷子を拾う。

    フック(vault-folder-guard.sh)は Write ツール経由しか見られないので、
    GUI 由来のものはここでしか検出できない。
    """
    if not VAULT.is_dir():
        return []
    out = []
    for p in sorted(VAULT.iterdir()):
        if p.is_dir() or p.name.startswith("."):
            continue
        if p.name in ROOT_ALLOWED:
            continue
        out.append(p.name)
    return out


def _hot_age_days() -> int:
    """hot.md の【手動部】の古さ。auto-updated(機械更新)ではなく updated を見る。

    機械が「最近の変更」を自動更新しても、人/Claude が書く「直近の事実」
    「進行中のスレッド」が古いままなら、それは古い文脈を配り続けている。
    """
    if not HOT.exists():
        return 0
    m = re.search(r"^updated:\s*(\d{4}-\d{2}-\d{2})", HOT.read_text(encoding="utf-8",
                  errors="replace"), re.MULTILINE)
    if not m:
        return 0
    try:
        d = dt.date.fromisoformat(m.group(1))
    except ValueError:
        return 0
    return (dt.date.today() - d).days


def _triage_days() -> int:
    """最後に /triage を回してからの日数。スタンプが無ければ Inbox の最古 mtime で代用。"""
    stamp = SCRIPTS / ".vault-triage-stamp"
    if stamp.exists():
        try:
            return int((time.time() - int(stamp.read_text().strip())) // 86400)
        except (ValueError, OSError):
            pass
    return _inbox()["oldest_days"]


def _weekly() -> dict:
    """今週の週次ノートの有無。週番号は vault-weekly.py の locale_week() に合わせる。"""
    spec = importlib.util.spec_from_file_location("vault_weekly", SCRIPTS / "vault-weekly.py")
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        y, w = mod.locale_week(dt.date.today())
    except Exception:  # noqa: BLE001 — 週次ノートの有無は補助表示。判定できないときは警告を出さない側に倒す
        return {"iso": "", "exists": True}
    name = f"{y}-W{w:02d}"
    return {"iso": name, "exists": (VAULT / "daily" / "weekly" / f"{name}.md").exists()}


def _study() -> dict:
    """Anki の想起状況(期限切れ枚数と、最後に思い出してからの日数)。

    実測 0.12s と、この中では重い部類(collection をコピーしてから読むため)。
    キャッシュに載せる前提で、ダッシュボードからは直接呼ばない。
    Anki が入っていない環境では available=False が返るだけで落ちない。
    """
    spec = importlib.util.spec_from_file_location(
        "vault_study_status", SCRIPTS / "vault-study-status.py")
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.analyze()
    except Exception:  # noqa: BLE001 — 状態表示の一項目。取れなくても他を巻き込まない
        return {"available": False}
    if not r.get("available"):
        return {"available": False}
    return {
        "available": True,
        "due": r["counts"]["due"],
        "new": r["counts"]["new"],
        "total": r["counts"]["total"],
        "stale_days": r["stale_days"],
        "days_studied_7": r["days_studied_7"],
        "last_recall": r["last_recall"],
    }


def build() -> dict:
    lint = _load_lint().analyze()
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "lint": {
            "critical": len(lint["critical"]),
            "warn": len(lint["warn"]),
            "suggest": len(lint["suggest"]),
            "orphan": len(lint["orphan"]),
            "notes": lint["notes"],
        },
        "inbox": _inbox(),
        "git": {"vault": _git_count(VAULT), "dotfiles": _git_count(DOTFILES)},
        "hot": {"manual_age_days": _hot_age_days()},
        "weekly": _weekly(),
        "root_junk": _root_junk(),
        "jobs": _jobs(),
        "triage_days_ago": _triage_days(),
        "study": _study(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="キャッシュを無視して再生成")
    ap.add_argument("--refresh", action="store_true", help="再生成して書くだけ(出力しない)")
    args = ap.parse_args()

    if args.refresh:
        CACHE.write_text(json.dumps(build(), ensure_ascii=False), encoding="utf-8")
        return 0

    if not args.fresh and CACHE.exists():
        age = time.time() - CACHE.stat().st_mtime
        if age < CACHE_TTL:
            print(CACHE.read_text(encoding="utf-8"))
            return 0

    data = build()
    # キャッシュの書き込み失敗は致命的でない(次回作り直す)ので握り潰す
    with contextlib.suppress(OSError):
        CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
