#!/usr/bin/env bash
# Vault のバックグラウンドジョブを名前で起動する単一の入口。
#
#   vault-bg-run.sh rss       # RSS 取得 → ローカル要約 → Inbox
#   vault-bg-run.sh weekly    # 週次ヘルスダイジェスト
#   vault-bg-run.sh hot-auto  # hot.md の自動ブロック更新
#
# なぜこれが要るか(2026-07-26):
# 同じジョブの起動が SessionStart フックと Templater(Obsidian)の2箇所に
# コピーされていて、両方が `/usr/bin/python3`(3.9.6)を絶対パスで指定していた。
# vault-rss.py が PEP 604 構文を持った瞬間、【両方】が同時に死んだ。しかも
# Templater 側は .obsidian/plugins/templater-obsidian/data.json の中の JSON 文字列で、
# 差分が追えずエスケープも読みにくいため、修正が片方だけに当たる事故が起きやすい。
#
# この1本を噛ませることで、以後の修正はシェル側で完結し、両経路が必ず同じ
# コードパスを通る。Templater からは `nohup .../vault-bg-run.sh rss &` だけを呼ぶ。
#
# 終了コードは vault_bg_run に準じる: 0=起動 / 1=ロック中 / 2=連続失敗で停止中 / 3=python無し

set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SCRIPTS="${CLAUDE_DIR:-$HOME/.claude}/scripts"
# shellcheck source=${DOTFILES:-$HOME/dotfiles}/.claude/hooks/lib/vault-common.sh
. ${CLAUDE_DIR:-$HOME/.claude}/hooks/lib/vault-common.sh

# --delay N: 実行前に N 秒待つ(子プロセスの中で待つので呼び出し側は待たない)。
# Templater から「ノートが作られる瞬間」に呼ぶときに使う。Obsidian がファイルを
# 書き終える前にスクリプトが読むと、空のノートを相手にしてしまうため。
# SessionStart フックからの起動は「ノートが既にある」ことが条件なので不要。
VAULT_BG_DELAY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --delay) VAULT_BG_DELAY="${2:-0}"; shift 2 ;;
    --) shift; break ;;
    -*) echo "unknown option: $1" >&2; exit 64 ;;
    *) break ;;
  esac
done
export VAULT_BG_DELAY

job="${1:-}"
[ -n "$job" ] || { echo "usage: vault-bg-run.sh [--delay N] <rss|weekly|hot-auto>" >&2; exit 64; }
shift || true

case "$job" in
  rss)      script="$SCRIPTS/vault-rss.py" ;;
  weekly)   script="$SCRIPTS/vault-weekly.py" ;;
  hot-auto) script="$SCRIPTS/vault-hot-auto.py" ;;
  *) echo "unknown job: $job" >&2; exit 64 ;;
esac

[ -f "$script" ] || { echo "script not found: $script" >&2; exit 66; }

log="$SCRIPTS/vault-${job}.log"

# インタプリタは「実際にバージョンを聞いて」決める。パスから推測しない。
py=$(vault_python) || {
  printf '{"ts":"%s","job":"%s","rc":3,"tail":"python3 >= 3.10 が見つからない"}\n' \
    "$(date +%FT%T)" "$job" >> "$SCRIPTS/.vault-failures.jsonl"
  exit 3
}

vault_bg_run "$job" "$log" "$py" "$script" "$@"
exit $?
