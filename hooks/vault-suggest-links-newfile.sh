#!/usr/bin/env bash
# PostToolUse フック: Vault 内に「新規作成」された .md ノートだけ、意味の近い
# 既存ノートをリンク候補として即座に提案する。既存ファイルの編集では発火しない
# (git status --porcelain で untracked 判定)。副産物として vault.db の索引も
# このファイル分だけ増分更新される。Ollama/索引が使えなければ黙って何もしない。
# stdin から JSON を受け取り、候補があれば hookSpecificOutput.additionalContext
# を返す。編集は止めない(常に exit 0)。

set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"
INDEX="$HOME/.claude/vault-search/vault_search.py"
SUGGEST="$HOME/.claude/scripts/vault-suggest-links.py"

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
[ -z "$file" ] && exit 0

case "$file" in
  "$VAULT"/*.md) ;;
  *) exit 0 ;;
esac
case "$file" in
  "$VAULT"/.obsidian/*|"$VAULT"/.trash/*|"$VAULT"/template/*|"$VAULT"/attachments/*) exit 0 ;;
esac

# 新規作成(untracked)のときだけ発火。既存ファイルの編集では何もしない
status=$(git -C "$VAULT" -c core.quotepath=false status --porcelain -- "$file" 2>/dev/null)
case "$status" in
  \?\?*) ;;
  *) exit 0 ;;
esac

command -v uv >/dev/null 2>&1 || exit 0
[ -f "$INDEX" ] && [ -f "$SUGGEST" ] || exit 0

# 増分索引(このファイル1件分。Ollama が止まっていれば数秒でエラー終了するだけ)
uv run --quiet "$INDEX" index >/dev/null 2>&1

out=$(uv run --quiet "$SUGGEST" --file "$file" 2>/dev/null) || exit 0
printf '%s' "$out" | grep -q '\[\[' || exit 0

jq -nc --arg out "$out" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: ("suggest-links(新規ノート): 意味の近い既存ノートへのリンク候補。\n" + $out)
  }
}'
