#!/usr/bin/env bash
# PreToolUse フック: 危険なコマンドを検知して確認(ask)または拒否(deny)を強制する。
# stdin から JSON を受け取り、hookSpecificOutput.permissionDecision を返す。

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

emit() {
  # $1 = allow|deny|ask, $2 = 理由
  jq -nc --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$d,permissionDecisionReason:$r}}'
  exit 0
}

# --- 破滅的: 無条件で拒否 ---
# rm -rf / , rm -rf ~ , rm -rf $HOME
# shellcheck disable=SC2016
# 単一引用符の中は grep に渡す正規表現。\$HOME は「検査対象のコマンド文字列に
# 現れるリテラルの $HOME」を指すので、ここで展開させてはいけない。
if printf '%s' "$cmd" | grep -Eq 'rm[[:space:]]+-[a-zA-Z]*[rf][a-zA-Z]*[[:space:]]+(/|~|\$HOME)([[:space:]]|$)'; then
  emit deny "破滅的な削除 (rm -rf / など) を検知したため拒否しました。必要なら手動で実行してください。"
fi
# main/master への force push
if printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+push[^;&|]*(--force|--force-with-lease|-f)[^;&|]*(main|master)'; then
  emit deny "保護ブランチ (main/master) への force push を検知したため拒否しました。必要なら手動で実行してください。"
fi

# --- 危険: 確認を要求 (ask) ---
reasons=""
add() { reasons="${reasons}${reasons:+ / }$1"; }

printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+push[^;&|]*(--force|--force-with-lease|-f([[:space:]]|$))' && add "force push"
printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+reset[^;&|]*--hard'                                    && add "reset --hard"
printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+clean[^;&|]*-[a-zA-Z]*f'                                && add "clean -f"
printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+branch[^;&|]*-D'                                        && add "branch -D (強制削除)"
printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+(filter-branch|filter-repo)'                            && add "履歴の書き換え"
printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+checkout[^;&|]*(-f([[:space:]]|$)|--force)'             && add "checkout --force"
printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])rm[[:space:]]+-[a-zA-Z]*[rf]'                           && add "rm -rf"

if [ -n "$reasons" ]; then
  emit ask "危険な操作 ($reasons) を検知しました。実行してよいか確認してください。"
fi

exit 0
