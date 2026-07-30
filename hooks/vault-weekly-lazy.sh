#!/usr/bin/env bash
# SessionStart フック: 「今週の週次ノートが作成済みで、まだ週次ダイジェストが
# 書き込まれていない」ときだけ、バックグラウンドで生成する。
#
# トリガーを「前回から7日」→「週次ノートの作成」に変更(2026-07-14)。
# ユーザーが週次レビューで週次ノートを作った週だけ、その次のセッションで
# ダイジェストがそのノート末尾に現れる。作らない週は走らない。旧方式のように
# 週次ノートを勝手に新規作成することもない(存在するノートに追記するだけ)。
#
# なぜ Templater(Obsidian)から直接叩かないか: launchd 同様、GUI から起動した
# プロセスも ~/Documents の TCC で落ちうる。Claude Code の文脈はターミナルの
# Documents アクセス権を継承するので確実。代償として「作成の瞬間」ではなく
# 「作成後の最初のセッション」になるが、この Vault では実質すぐ。
#
# stdout には何も出さない(hot-cache フックの additionalContext を汚さないため)。

set -u
# バックグラウンド起動でも uv / git / python3 が解決するよう PATH を明示
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"
VAULT_GUARD=$((30 * 60))   # 二重起動防止: 直近30分に起動済みなら走らせない
export VAULT VAULT_GUARD

# shellcheck source=lib/vault-common.sh
. ${CLAUDE_DIR:-$HOME/.claude}/hooks/lib/vault-common.sh

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
vault_scope_check "$cwd" || exit 0
vault_real=$(cd "$VAULT" && pwd -P)

# 今週の週次ノート。命名は Templater / Periodic Notes と同じ moment の locale week
# (週の始まり=日曜)。vault-weekly.py の locale_week() と必ず一致させること。
#
# 以前ここは `date +%G-W%V`(ISO 週 = 月曜始まり)だった。ISO と locale は
# 月〜土は一致するが【日曜だけ 1 ずれる】ため、日曜に走ると隣の週のノートを
# 掴んでいた。2026-07-26(日)に実際の取り違えとして発覚。
NOTE="$vault_real/daily/weekly/$("$(dirname "$0")/../scripts/vault-weekly.py" --print-week 2>/dev/null || true).md"
[ "$NOTE" = "$vault_real/daily/weekly/.md" ] && exit 0

# トリガー(2026-07-26 変更): 従来は「週次ノートが既に存在すること」が条件だった。
# しかし週次ノートは人が Periodic Notes で作るもので、実際には W18/W28/W29 の3本しか
# 作られず、このフックは一度も自動発火していなかった(スタンプの最終更新は 7/13)。
#
# 週の後半なら、ノートが無くても vault-weekly.py に作らせる。locale 週は日曜始まり
# なので「後半」= 金(5)・土(6)。本人の運用も W29 を金曜に作っている。
# 日曜〜木曜は従来どおり「既にあるノートへの追記」だけ(週頭に空の器を作らない)。
dow_locale=$(( ($(date +%u) ) % 7 ))   # 0=日 1=月 … 6=土
if [ ! -f "$NOTE" ] && [ "$dow_locale" -lt 5 ]; then exit 0; fi
# すでにダイジェストが入っていれば何もしない(冪等)
grep -q 'vault-weekly:start' "$NOTE" 2>/dev/null && exit 0

# 起動は vault-bg-run.sh に委譲する。失敗時はスタンプが進まないので次回再試行される。
${CLAUDE_DIR:-$HOME/.claude}/scripts/vault-bg-run.sh weekly >/dev/null 2>&1
exit 0
