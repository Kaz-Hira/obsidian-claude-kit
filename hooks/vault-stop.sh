#!/usr/bin/env bash
# Stop フック: セッション終了時に3つの責務を【独立に】判定する。
#
#   1. hot.md の自動ブロックを更新する(無条件・無言・バックグラウンド)
#   2. 未コミットの変更を知らせる(閾値つき)
#   3. hot.md の【手動部】の更新を Claude に促す(条件つき・1回だけ)
#
# 旧 vault-uncommitted-reminder.sh からの変更点(2026-07-26):
# 旧版は「未コミットが1件でもあれば無条件に decision:block」だった。実際には
# Vault に21件が常駐していたので【毎セッション必ず鳴り】、21件が22件になっても
# 文面が変わらない。人間は3回で読まなくなる。実際 hot.md には「未コミット2件」と
# 6日前の数字が書かれたまま放置されていた——通知は出ていたが行動に繋がっておらず、
# リマインダーの設計そのものが空振りしていた。
#
# 3 が /hot 問題の本質的な解。`/hot` の実行が3回で止まったのは「人間が思い出す
# 必要がある」から。Stop フックが【Claude に】指示すれば人間の記憶に依存しない。
#
# 非破壊。コミットは一切しない。

set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"
DOTFILES="${DOTFILES:-$HOME/dotfiles}"
SCRIPTS="${CLAUDE_DIR:-$HOME/.claude}/scripts"
UNCOMMITTED_THRESHOLD=15   # これ未満なら黙る(常時20件前後あるので1件で鳴らさない)
HOT_STALE_DAYS=3

export VAULT
# shellcheck source=lib/vault-common.sh
. ${CLAUDE_DIR:-$HOME/.claude}/hooks/lib/vault-common.sh
# shellcheck source=lib/vault-nudge.sh
. ${CLAUDE_DIR:-$HOME/.claude}/hooks/lib/vault-nudge.sh

input=$(cat)

# 既に一度知らせた後の再停止なら何もしない(無限ループ防止)
active=$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$active" = "true" ] && exit 0

cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
vault_scope_check "$cwd" || exit 0
vault_real=$(cd "$VAULT" && pwd -P)

# --- 1. hot.md の自動ブロック更新(無条件・無言) -------------------------
# 機械が導出できる「最近の変更」だけを更新する。updated: は触らないので、
# 手動部の鮮度判定(下の 3)は汚れない。
if [ -f "$SCRIPTS/vault-hot-auto.py" ]; then
  "$SCRIPTS/vault-bg-run.sh" hot-auto >/dev/null 2>&1 || true
fi

# --- 材料を集める -----------------------------------------------------------
vault_n=$(cd "$vault_real" && git status --porcelain 2>/dev/null | grep -c . || echo 0)
dot_n=$(cd "$DOTFILES" 2>/dev/null && git status --porcelain 2>/dev/null | grep -c . || echo 0)
total=$(( vault_n + dot_n ))

# 未追跡の .md = このセッションで生まれた可能性のある新規ノート。
# 件数に関係なく一度は知らせる(ノートの消失は実害があるので鳴らす価値がある)。
# core.quotepath=false が無いと日本語ファイル名が \343\203\236 のような
# 8進エスケープで出て、通知が読めなくなる(日本語ノートが大半なので致命的)
new_notes=$(cd "$vault_real" && git -c core.quotepath=false ls-files --others --exclude-standard -- '*.md' 2>/dev/null | head -5)
new_n=$(printf '%s' "$new_notes" | grep -c . || echo 0)

# hot.md 手動部の古さ(updated: を見る。auto-updated: ではない)
hot_age=0
if [ -f "$vault_real/hot.md" ]; then
  d=$(grep -m1 '^updated:' "$vault_real/hot.md" 2>/dev/null | sed 's/^updated:[[:space:]]*//')
  if [ -n "$d" ]; then
    then_s=$(date -j -f "%Y-%m-%d" "$d" +%s 2>/dev/null || echo 0)
    [ "$then_s" -gt 0 ] && hot_age=$(( ( $(date +%s) - then_s ) / 86400 ))
  fi
fi

reasons=""

# --- 2. 未コミットの通知 ----------------------------------------------------
# 新規ノートと未コミット件数は【どちらもコミットの話】なので、抑制キーを1つに
# まとめる。別々にすると「1回目は新規ノート、2回目は件数」と連続して鳴り、
# 潰したはずの警報疲れが戻る(Stop で鳴らすのは最大1件、という上限を守る)。
#
# 出す条件: 未追跡の新規ノートがある(件数不問。ノート消失は実害がある)
#           または 未コミット総数が閾値以上
commit_msg=""
if [ "$new_n" -gt 0 ]; then
  commit_msg="- 未追跡の新規ノートが ${new_n} 件(未コミット合計 Vault ${vault_n}件 / dotfiles ${dot_n}件):
$(printf '%s\n' "$new_notes" | sed 's/^/    /')"
elif [ "$total" -ge "$UNCOMMITTED_THRESHOLD" ]; then
  commit_msg="- 未コミットの変更: Vault ${vault_n}件 / dotfiles ${dot_n}件"
fi

if [ -n "$commit_msg" ]; then
  # 新規ノートがあるときは閾値1(件数不問)、無いときは通常の閾値
  gate=$([ "$new_n" -gt 0 ] && echo 1 || echo "$UNCOMMITTED_THRESHOLD")
  metric=$([ "$new_n" -gt 0 ] && echo "$new_n" || echo "$total")
  if nudge_should_emit "stop_commit" "$metric" "$gate" 1; then
    reasons="${reasons}${commit_msg}
"
    nudge_record "stop_commit" "$metric"
  fi
else
  nudge_reset "stop_commit"
fi

# --- 3. hot.md 手動部の更新催促 ---------------------------------------------
# 「このセッションで Vault に手を入れた」かつ「手動部が3日以上古い」とき。
# updated: が更新されれば次回は条件を満たさなくなるので、二重発火しない。
if [ "$hot_age" -ge "$HOT_STALE_DAYS" ] && [ "$vault_n" -ge 3 ] \
   && nudge_should_emit "stop_hot" "$hot_age" "$HOT_STALE_DAYS" 1; then
  reasons="${reasons}- \`hot.md\` の手動部が ${hot_age} 日古いままです。このセッションの内容で
  「## 直近の事実」と「## 進行中のスレッド」を各3行以内に書き換え、frontmatter の
  \`updated:\` を今日にしてください。「## 最近の変更」は機械が自動更新しているので
  触らないこと(hot:auto マーカーの中身)。既に解決した項目は削除する——
  hot.md は毎セッション注入されるので、古い TODO を残すと配り続けることになります。
"
  nudge_record "stop_hot" "$hot_age"
fi

[ -z "$reasons" ] && exit 0

jq -nc --arg r "$reasons" '{
  decision: "block",
  reason: ("セッション終了前に一点だけ。ユーザーに一文で伝えてから終了してください(自動コミットはしないこと)。\n" + $r)
}'
exit 0
