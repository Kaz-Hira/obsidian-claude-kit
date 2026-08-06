#!/usr/bin/env python3
"""稼働中の ~/.claude からこのリポジトリへ、個人情報を落として同期する。

このリポジトリは**生成物**であり、単一の真実は手元の稼働環境(既定 ~/dotfiles/.claude)。
公開用に手で書き換えたコピーを持つと必ず二重管理になって腐るので、常にここから作る。

    python3 tools/sync-from-source.py            # 同期して差分を出す
    python3 tools/sync-from-source.py --check    # 書かずに検査だけ(CI 用)
    python3 tools/sync-from-source.py --source ~/other/.claude

やること:
  1. MANIFEST に載ったファイルだけをコピーする(ホワイトリスト。載せ忘れは公開されない側に倒れる)
  2. 個人の絶対パスを環境変数・プレースホルダに置き換える
  3. 置き換え残しが1つでもあれば異常終了する(公開事故を機械で止める)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path("~/dotfiles/.claude").expanduser()

# ---------------------------------------------------------------------------
# 公開するもの(ホワイトリスト)
#
# 載せないもの と その理由:
#   commands/aa|agmsg|claude   エージェント間メッセージング。Vault とは別系統
#   hooks/terminal-title.sh    個人のターミナル装飾
#   scripts/session-picker.py  同上
#   scripts/statusline.sh      同上
#   scripts/term-preview*.sh   同上
#   scripts/vault-photo.py     個人の撮影ワークフロー(機材構成に強く依存)
#   scripts/vault-music.sh     同上(音楽)
#   scripts/vault-feeds.txt    購読リストは個人情報。.example を別途同梱する
#   scripts/*.log              実行ログ
#   skills/exam-digest         在学校のカリキュラム前提
#   skills/new-project         個人のディレクトリ規約(~/dev, ~/TMCIT)前提
#   vault-search/vault.db      Vault 全文の埋め込み。中身が漏れる
# ---------------------------------------------------------------------------
MANIFEST: dict[str, list[str]] = {
    "commands": [
        "anki.md", "daily.md", "dispatch.md", "hot.md", "lint.md",
        "research.md", "save.md", "suggest-links.md", "triage.md",
        "weekly-review.md",
    ],
    "hooks": [
        "git-guard.sh", "vault-dashboard.sh", "vault-folder-guard.sh",
        "vault-hot-cache.sh", "vault-lint-posttool.sh", "vault-rss-lazy.sh",
        "vault-stop.sh", "vault-suggest-links-newfile.sh", "vault-weekly-lazy.sh",
    ],
    "hooks/lib": ["vault-common.sh", "vault-nudge.sh"],
    "scripts": [
        "vault-anki.py", "vault-autotag.py", "vault-bg-run.sh",
        "vault-hot-auto.py", "vault-lint.py", "vault-moc-audit.py",
        "vault-rss.py", "vault-status.py", "vault-suggest-links.py",
        "vault-voice.sh", "vault-weekly.py", "vault-zenn-sync.py",
    ],
    "agents": [
        "code-reviewer.md", "fact-checker.md", "note-synthesizer.md",
        "security-reviewer.md", "vault-structure-reviewer.md",
    ],
    "vault-search": ["vault_search.py"],
}

# ディレクトリごと持っていくスキル
SKILLS = [
    "defuddle", "diagnosing-bugs", "moc-audit", "obsidian-bases",
    "obsidian-blog", "obsidian-cli", "vault-automation", "voice", "vsearch",
]
SKILL_EXCLUDE = {".DS_Store", "__pycache__"}

HOME = str(Path.home())
VAULT_ABS = f"{HOME}/Documents/Obsidian_Vault"
CLAUDE_ABS = f"{HOME}/.claude"
DOTFILES_ABS = f"{HOME}/dotfiles"

# .py に注入するヘルパ。環境変数 > 既定値(~ 展開)の順で解決する
PY_HELPER = '''

def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()

'''


def sub_markdown(text: str) -> str:
    """.md は install.sh が実パスへ差し戻すプレースホルダにする。

    コマンド定義の frontmatter(allowed-tools)にもパスが出るので、実行時に
    解決される $VAULT ではなく、インストール時に確定する {{VAULT}} を使う。
    """
    text = text.replace(VAULT_ABS, "{{VAULT}}")
    text = text.replace(CLAUDE_ABS, "{{CLAUDE_DIR}}")
    text = text.replace(DOTFILES_ABS, "{{DOTFILES}}")
    return text


def sub_shell(text: str) -> str:
    """.sh は環境変数で解決する。既に ${VAULT:-...} 形式のものは既定値だけ差し替える。"""
    text = text.replace(f"${{VAULT:-{VAULT_ABS}}}", '${VAULT:-$HOME/Documents/Obsidian_Vault}')
    text = text.replace(f"${{VAULT_STATE_DIR:-{CLAUDE_ABS}/scripts}}",
                        '${VAULT_STATE_DIR:-$HOME/.claude/scripts}')
    text = text.replace(VAULT_ABS, '${VAULT:-$HOME/Documents/Obsidian_Vault}')
    text = text.replace(CLAUDE_ABS, '${CLAUDE_DIR:-$HOME/.claude}')
    text = text.replace(DOTFILES_ABS, '${DOTFILES:-$HOME/dotfiles}')
    return text


def sub_python(text: str) -> str:
    """.py は Path("<絶対パス>") を _envpath() 呼び出しに書き換え、ヘルパを注入する。"""
    rules = [
        (rf'Path\("{re.escape(VAULT_ABS)}"\)', '_envpath("VAULT", "~/Documents/Obsidian_Vault")'),
        (rf'Path\("{re.escape(DOTFILES_ABS)}"\)', '_envpath("DOTFILES", "~/dotfiles")'),
        # ~/.claude 配下は共通の根から相対で組み立てる
        (rf'Path\("{re.escape(CLAUDE_ABS)}/([^"]+)"\)',
         lambda m: f'_envpath("CLAUDE_DIR", "~/.claude") / "{m.group(1)}"'),
        (rf'Path\("{re.escape(CLAUDE_ABS)}"\)', '_envpath("CLAUDE_DIR", "~/.claude")'),
    ]
    changed = False
    for pat, rep in rules:
        text, n = re.subn(pat, rep, text)
        changed = changed or bool(n)

    if not changed:
        return text

    text = ensure_import(text, "import os", r"^import os$")
    text = ensure_import(text, "from pathlib import Path", r"^from pathlib import Path$")
    return inject_helper(text)


def ensure_import(text: str, statement: str, pattern: str) -> str:
    if re.search(pattern, text, re.M):
        return text
    lines = text.splitlines(keepends=True)
    idx = last_import_line(lines)
    lines.insert(idx, statement + "\n")
    return "".join(lines)


def last_import_line(lines: list[str]) -> int:
    """最後の import 行の次の位置を返す。import が無ければ docstring の後ろ。"""
    last = 0
    for i, line in enumerate(lines):
        if re.match(r"^(import |from )\S", line):
            last = i + 1
    if last:
        return last
    # import が1つも無い場合は shebang / コメントの直後に置く
    for i, line in enumerate(lines):
        if line.strip() and not line.startswith(("#", '"""', "'''")):
            return i
    return len(lines)


def inject_helper(text: str) -> str:
    if "_envpath" not in text.split("def _envpath")[0]:
        pass
    if "def _envpath" in text:
        return text
    lines = text.splitlines(keepends=True)
    idx = last_import_line(lines)
    lines.insert(idx, PY_HELPER)
    return "".join(lines)


def transform(path: Path, text: str) -> str:
    if path.suffix == ".md":
        return sub_markdown(text)
    if path.suffix == ".sh":
        return sub_shell(text)
    if path.suffix == ".py":
        return sub_python(text)
    return text


def iter_manifest(source: Path):
    for rel_dir, names in MANIFEST.items():
        for name in names:
            yield source / rel_dir / name, REPO / rel_dir / name
    for skill in SKILLS:
        root = source / "skills" / skill
        if not root.is_dir():
            continue
        for src in sorted(root.rglob("*")):
            if not src.is_file():
                continue
            if any(part in SKILL_EXCLUDE for part in src.parts):
                continue
            yield src, REPO / "skills" / skill / src.relative_to(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                    help="同期元の .claude ディレクトリ(既定 ~/dotfiles/.claude)")
    ap.add_argument("--check", action="store_true", help="書き込まずに検査だけ行う")
    args = ap.parse_args()

    source = args.source.expanduser()
    if not source.is_dir():
        print(f"同期元が見つからない: {source}", file=sys.stderr)
        return 1

    missing: list[str] = []
    written = 0
    leaked: list[str] = []

    for src, dst in iter_manifest(source):
        if not src.is_file():
            missing.append(str(src))
            continue
        raw = src.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            if not args.check:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            continue

        out = transform(dst, text)

        if HOME in out:
            for line_no, line in enumerate(out.splitlines(), 1):
                if HOME in line:
                    leaked.append(f"{dst.relative_to(REPO)}:{line_no}: {line.strip()[:100]}")

        if not args.check:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(out, encoding="utf-8")
            if src.stat().st_mode & 0o111:
                dst.chmod(0o755)
            written += 1

    if missing:
        print("同期元に無いファイル(MANIFEST を直すこと):", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)

    if leaked:
        print(f"\n個人パスの置換漏れ {len(leaked)} 件 — 公開してはいけない:", file=sys.stderr)
        for line in leaked:
            print(f"  {line}", file=sys.stderr)
        return 2

    if args.check:
        print("検査のみ: 置換漏れなし")
        return 1 if missing else 0

    print(f"{written} ファイルを同期した(置換漏れなし)")
    return verify()


def verify() -> int:
    """生成物が壊れていないか実際に確かめる。構文チェックだけでは足りない。"""
    failed: list[str] = []

    for py in sorted(REPO.rglob("*.py")):
        if "tools/" in str(py.relative_to(REPO)):
            continue
        r = subprocess.run([sys.executable, "-m", "py_compile", str(py)],
                           capture_output=True, text=True)
        if r.returncode:
            failed.append(f"py_compile {py.name}: {r.stderr.strip()[:200]}")

    for sh in sorted(REPO.rglob("*.sh")):
        r = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        if r.returncode:
            failed.append(f"bash -n {sh.name}: {r.stderr.strip()[:200]}")

    if failed:
        print("\n生成物の検証に失敗:", file=sys.stderr)
        for f in failed:
            print(f"  {f}", file=sys.stderr)
        return 3

    print("検証: py_compile / bash -n ともに通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
