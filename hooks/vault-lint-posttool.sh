#!/usr/bin/env bash
# PostToolUse フック: Vault 内の .md を Edit/Write した直後に、そのファイルだけを lint する。
# stdin から JSON を受け取り、違反があれば hookSpecificOutput.additionalContext で知らせる。
# 編集は止めない(常に exit 0)。Vault 外のファイルには何もしない。

set -u

VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"
LINT="$HOME/.claude/scripts/vault-lint.py"

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
[ -z "$file" ] && exit 0

# Vault 配下の .md のみ対象(.obsidian/ .trash/ template/ の除外は lint 側が行う)
case "$file" in
  "$VAULT"/*.md) ;;
  *) exit 0 ;;
esac

out=$("$LINT" --file "$file" 2>/dev/null) || exit 0
[ -z "$out" ] && exit 0

jq -nc --arg out "$out" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: ("vault-lint(編集したファイルのみ検査): 規約違反を検出。意図的でなければ直すこと。\n" + $out)
  }
}'
