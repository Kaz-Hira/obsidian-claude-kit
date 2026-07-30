#!/usr/bin/env bash
# SessionStart フック: 「今日のデイリーノートが作成済みで、まだ今日の RSS
# ダイジェストが無い」ときだけ、バックグラウンドで RSS 取得・要約を走らせる。
#
# トリガーを「launchd 毎日9:00」→「デイリーノートの作成」に変更(2026-07-15)。
# launchd 起動プロセスは ~/Documents の TCC で落ちるため毎朝黙って失敗していた
# ([[週次ヘルスダイジェスト]] と同じ問題)。デイリーノートを作った日だけ、その
# 次のセッションで RSS ダイジェストが Inbox に現れる。作らない日は走らない。
#
# 正規のトリガーは Templater(daily_rss)で「作成の瞬間」に走る。これはその保険:
# Obsidian(GUI)起動の Templater が TCC で落ちても、次セッションで確実に埋める。
# vault-rss.py は Inbox/YYYYMMDD-RSS.md に書くので、下の存在チェックが冪等性を担う。
#
# stdout には何も出さない(hot-cache フックの additionalContext を汚さないため)。

set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"
VAULT_GUARD=$((15 * 60))   # 二重起動防止: 直近15分に起動済みなら走らせない
export VAULT VAULT_GUARD

# shellcheck source=lib/vault-common.sh
. ${CLAUDE_DIR:-$HOME/.claude}/hooks/lib/vault-common.sh

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
vault_scope_check "$cwd" || exit 0
vault_real=$(cd "$VAULT" && pwd -P)

# 今日のデイリーノート(periodic-notes の daily/diary/YYYY-MM-DD.md)
DAILY="$vault_real/daily/diary/$(date +%F).md"
# 今日の RSS ダイジェスト(vault-rss.py の出力先と同じ命名)
RSS="$vault_real/Inbox/$(date +%Y%m%d)-RSS.md"

# トリガーは「デイリーノートの作成」。未作成なら何もしない
[ -f "$DAILY" ] || exit 0
# すでに今日の RSS があれば何もしない(冪等)
[ -f "$RSS" ] && exit 0

# 起動は vault-bg-run.sh に委譲する(2026-07-26)。Templater 側も同じ1本を呼ぶので、
# インタプリタ解決もスタンプ管理も両経路が必ず同じコードパスを通る。
# 失敗した場合スタンプは進まないので、次のセッションで自動的に再試行される。
${CLAUDE_DIR:-$HOME/.claude}/scripts/vault-bg-run.sh rss >/dev/null 2>&1
exit 0
