#!/usr/bin/env bash
#
# obsidian-claude-kit のインストーラ。
#
#   ./install.sh --vault ~/Documents/MyVault            # 導入
#   ./install.sh --vault ~/Documents/MyVault --dry-run  # 何が起きるか見るだけ
#   ./install.sh --vault ~/Documents/MyVault --no-settings
#
# 既存ファイルは上書きする前に必ず退避する(~/.claude/backups/kit-<日時>/)。
# settings.json は既存の内容を残したままマージする。壊すくらいなら何もしない。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
VAULT=""
DRY_RUN=0
DO_SETTINGS=1

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-settings) DO_SETTINGS=0; shift ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "不明な引数: $1" ;;
  esac
done

# ---------------------------------------------------------------------------
# 前提の確認。ここで落とすほうが、入れてから動かないより親切
# ---------------------------------------------------------------------------
step "前提を確認する"

[ -n "$VAULT" ] || die "--vault で Obsidian Vault のパスを指定すること"
VAULT="$(cd "${VAULT/#\~/$HOME}" 2>/dev/null && pwd -P)" || die "Vault が見つからない: $VAULT"
[ -d "$VAULT/.obsidian" ] || printf '\033[33mwarning:\033[0m %s に .obsidian が無い。本当に Vault か確認すること\n' "$VAULT"
info "vault      = $VAULT"
info "claude dir = $CLAUDE_DIR"

PYTHON=""
for c in /opt/homebrew/bin/python3 "$(command -v python3 2>/dev/null || true)" /usr/bin/python3; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PYTHON="$c"; break
  fi
done
[ -n "$PYTHON" ] || die "python3 >= 3.10 が見つからない(brew install python)"
info "python     = $PYTHON ($("$PYTHON" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))'))"

command -v uv >/dev/null 2>&1 || printf '\033[33mwarning:\033[0m uv が無い。/vsearch と /anki は動かない(brew install uv)\n'

# ---------------------------------------------------------------------------
# 配置。{{VAULT}} 等のプレースホルダを実パスへ差し戻しながら書く
# ---------------------------------------------------------------------------
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$CLAUDE_DIR/backups/kit-$STAMP"

subst() {
  sed -e "s|{{VAULT}}|$VAULT|g" \
      -e "s|{{CLAUDE_DIR}}|$CLAUDE_DIR|g" \
      -e "s|{{DOTFILES}}|$HOME/dotfiles|g"
}

install_one() {
  # local は全引数を展開してから代入するので、同じ行で $rel を参照すると
  # 外側の変数を拾ってしまう(shellcheck SC2318)。dst は必ず別の local にする。
  local src="$1" rel="$2"
  local dst="$CLAUDE_DIR/$rel"

  if [ "$DRY_RUN" = 1 ]; then
    if [ -e "$dst" ]; then info "上書き(退避あり) $rel"; else info "新規          $rel"; fi
    return
  fi

  if [ -e "$dst" ] || [ -L "$dst" ]; then
    mkdir -p "$BACKUP/$(dirname "$rel")"
    cp -RL "$dst" "$BACKUP/$rel" 2>/dev/null || cp -R "$dst" "$BACKUP/$rel"
  fi

  mkdir -p "$(dirname "$dst")"
  # symlink 先を書き換えて元(dotfiles 等)を汚さないよう、必ず消してから作る
  rm -f "$dst"
  # テキストだけ置換する。画像などバイナリを同梱したスキルが来ても、
  # sed が "illegal byte sequence" で止まって導入全体を巻き添えにしないように。
  if subst < "$src" > "$dst" 2>/dev/null; then
    :
  else
    cp "$src" "$dst"
  fi
  [ -x "$src" ] && chmod 755 "$dst" || true
}

step "ファイルを配置する"
COUNT=0
while IFS= read -r src; do
  rel="${src#"$REPO"/}"
  case "$rel" in
    commands/*|hooks/*|scripts/*|skills/*|agents/*|vault-search/*) ;;
    *) continue ;;
  esac
  install_one "$src" "$rel"
  COUNT=$((COUNT + 1))
done < <(find "$REPO" \
  \( -path "$REPO/.git" -o -path "$REPO/tools" -o -path "$REPO/docs" \
     -o -path "$REPO/.github" -o -name "__pycache__" \) -prune -o \
  -type f ! -name "*.pyc" ! -name ".DS_Store" -print | sort)

info "$COUNT ファイル"
[ "$DRY_RUN" = 1 ] || [ ! -d "$BACKUP" ] || info "退避先: $BACKUP"

# 購読リストは初回だけ置く(既にあるものは触らない)
if [ "$DRY_RUN" = 0 ] && [ ! -f "$CLAUDE_DIR/scripts/vault-feeds.txt" ] \
   && [ -f "$REPO/scripts/vault-feeds.example.txt" ]; then
  cp "$REPO/scripts/vault-feeds.example.txt" "$CLAUDE_DIR/scripts/vault-feeds.txt"
  info "vault-feeds.txt を example から作成(購読先は自分で書き換えること)"
fi

# ---------------------------------------------------------------------------
# settings.json のマージ。既存のキーは消さない
# ---------------------------------------------------------------------------
if [ "$DO_SETTINGS" = 1 ]; then
  step "settings.json をマージする"
  if [ "$DRY_RUN" = 1 ]; then
    info "hooks(PreToolUse/PostToolUse/Stop/SessionStart)と permissions.allow を追記する"
  else
    subst < "$REPO/settings.example.json" > "$CLAUDE_DIR/.kit-settings.tmp.json"
    "$PYTHON" - "$CLAUDE_DIR" "$BACKUP" <<'PY'
import json, os, shutil, sys
from pathlib import Path

claude_dir, backup = Path(sys.argv[1]), Path(sys.argv[2])
target = claude_dir / "settings.json"
incoming = json.loads((claude_dir / ".kit-settings.tmp.json").read_text(encoding="utf-8"))
incoming.pop("_comment", None)

current = {}
if target.exists():
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup / "settings.json")
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  既存 settings.json が壊れている({e})。マージを中止した", file=sys.stderr)
        (claude_dir / ".kit-settings.tmp.json").unlink()
        raise SystemExit(1)

def merge_hooks(cur, inc):
    """同じ matcher の枠があればその中の hooks に足す。command の重複は入れない。"""
    out = list(cur)
    for entry in inc:
        matcher = entry.get("matcher")
        slot = next((e for e in out if e.get("matcher") == matcher), None)
        if slot is None:
            out.append(entry)
            continue
        have = {h.get("command") for h in slot.setdefault("hooks", [])}
        slot["hooks"].extend(h for h in entry.get("hooks", []) if h.get("command") not in have)
    return out

added = 0
ch = current.setdefault("hooks", {})
for event, entries in incoming.get("hooks", {}).items():
    before = json.dumps(ch.get(event, []), sort_keys=True)
    ch[event] = merge_hooks(ch.get(event, []), entries)
    if json.dumps(ch[event], sort_keys=True) != before:
        added += 1

allow = current.setdefault("permissions", {}).setdefault("allow", [])
for rule in incoming.get("permissions", {}).get("allow", []):
    if rule not in allow:
        allow.append(rule)
        added += 1

current.setdefault("env", {}).update(incoming.get("env", {}))

target.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(claude_dir / ".kit-settings.tmp.json").unlink()
print(f"  {'変更なし' if added == 0 else f'{added} 項目を追加'}(既存の設定は残した)")
PY
  fi
fi

# ---------------------------------------------------------------------------
step "動くか確かめる"
# 構文チェックでは足りない(PEP 604 の型注釈は py_compile を通って def 実行時に落ちる)。
# 実際に走らせて確認する。
if [ "$DRY_RUN" = 1 ]; then
  info "(dry-run のため実行しない)"
else
  err="$(mktemp)"
  VAULT="$VAULT" "$PYTHON" "$CLAUDE_DIR/scripts/vault-lint.py" >/dev/null 2>"$err" || rc=$?
  rc="${rc:-0}"
  # 0 = 指摘なし / 1 = 指摘あり。どちらも「走った」。2 以上と traceback だけが異常
  if [ "$rc" -le 1 ] && ! grep -q "Traceback" "$err"; then
    info "vault-lint.py: 実行できた(終了コード $rc)"
  else
    printf '\033[33mwarning:\033[0m vault-lint.py が異常終了した(rc=%s)\n' "$rc"
    head -n 5 "$err" | sed 's/^/    /'
  fi
  rm -f "$err"
fi

cat <<EOF

$( [ "$DRY_RUN" = 1 ] && echo "dry-run 完了。実際に入れるには --dry-run を外す。" || echo "導入完了。" )

次にやること:
  1. Vault 側に規約ファイルを置く — docs/vault-conventions.md を参考に <vault>/CLAUDE.md を書く
     フォルダ構成(Reference/ System/ Study/ ...)とタグ規約はここで定義する
  2. Vault で claude を起動する。SessionStart フックが hot.md を注入する
  3. /lint で健全性を見る → /triage で Inbox を仕分ける

セマンティック検索(/vsearch /suggest-links)を使うなら:
  ollama pull embeddinggemma && uv run $CLAUDE_DIR/vault-search/vault_search.py index
EOF
