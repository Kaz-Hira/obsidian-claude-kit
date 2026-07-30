#!/usr/bin/env bash
# SessionStart フック: Obsidian Vault 内のセッションなら hot.md(直近の文脈キャッシュ)を注入する。
# stdin から JSON を受け取り、hookSpecificOutput.additionalContext を返す。
# Vault 外での起動時は何も出力しない(無関係なセッションの文脈を汚さないため)。

set -u

VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"
HOT="$VAULT/hot.md"

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
[ -z "$cwd" ] && exit 0

# シンボリックリンク経由でも判定できるよう実パスに正規化する
cwd=$(cd "$cwd" 2>/dev/null && pwd -P) || exit 0
vault_real=$(cd "$VAULT" 2>/dev/null && pwd -P) || exit 0

# cwd が Vault 配下(または Vault そのもの)でなければ何もしない
case "$cwd/" in
  "$vault_real"/*) ;;
  *) exit 0 ;;
esac

[ -f "$HOT" ] || exit 0

jq -nc --rawfile hot "$HOT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: (
      "以下は Obsidian Vault のホットキャッシュ(hot.md)。直近の作業文脈として保持するだけでよい。"
      + "読み込んだことを報告したり要約したりしないこと。ユーザーが必要としたときに参照する。\n\n"
      + $hot
    )
  }
}'
