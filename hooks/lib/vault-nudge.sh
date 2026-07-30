#!/usr/bin/env bash
# 通知の抑制ライブラリ。source して使う(実行しない)。
#
#   nudge_should_emit <key> <value> <threshold> [min_days]   # 0 = 出してよい
#   nudge_record      <key> <value>                          # 出した事実を記録
#   nudge_reset       <key>                                  # 記録を消す
#
# 規則は「状態が変わったときだけ喋る」。
#
# 背景(2026-07-26):
# Stop フックの未コミットリマインダーは「1件でもあれば無条件で block」だった。
# 実際には Vault に21件が常駐していたので【毎セッション必ず鳴り】、しかも
# 21件が22件になっても文面が変わらない。人間は3回で読まなくなる。通知が
# 状態を反映していないと、警報疲れで通知そのものが死ぬ。
#
# 出してよい条件(すべて満たす):
#   1. value >= threshold                     … そもそも閾値を超えている
#   2. 次のいずれか
#      - まだ一度も出していない
#      - 前回の通知から min_days 日以上経っている(既定1日)
#      - 前回値から 1.5 倍以上悪化した(急変は日をまたがなくても知らせる)
#
# 状態は ~/.claude/scripts/.vault-nudge.json に {key: {value, ts}} で持つ。

NUDGE_STATE="${NUDGE_STATE:-${CLAUDE_DIR:-$HOME/.claude}/scripts/.vault-nudge.json}"

_nudge_get() {
  # $1 = key, $2 = field(value|ts) → 無ければ空文字
  [ -f "$NUDGE_STATE" ] || return 0
  jq -r --arg k "$1" --arg f "$2" '.[$k][$f] // empty' "$NUDGE_STATE" 2>/dev/null
}

nudge_should_emit() {
  local key="$1" value="$2" threshold="$3" min_days="${4:-1}"
  # 数値でないものは出さない(壊れた入力で鳴り続けるのを防ぐ)
  case "$value$threshold" in *[!0-9]*) return 1 ;; esac

  [ "$value" -ge "$threshold" ] || return 1

  local prev_v prev_ts
  prev_v=$(_nudge_get "$key" value)
  prev_ts=$(_nudge_get "$key" ts)
  # 初回は必ず出す
  [ -z "$prev_v" ] || [ -z "$prev_ts" ] && return 0

  # 前回から min_days 経っていれば出す
  local age_days=$(( ( $(date +%s) - prev_ts ) / 86400 ))
  [ "$age_days" -ge "$min_days" ] && return 0

  # 同じ日でも、1.5倍以上悪化したら出す(10 → 15 は知らせる価値がある)
  [ $(( value * 2 )) -ge $(( prev_v * 3 )) ] && return 0

  return 1
}

nudge_record() {
  local key="$1" value="$2" tmp
  tmp="${NUDGE_STATE}.tmp.$$"
  [ -f "$NUDGE_STATE" ] || echo '{}' > "$NUDGE_STATE"
  if jq --arg k "$key" --argjson v "${value:-0}" --argjson t "$(date +%s)" \
       '.[$k] = {value: $v, ts: $t}' "$NUDGE_STATE" > "$tmp" 2>/dev/null; then
    mv "$tmp" "$NUDGE_STATE"
  else
    rm -f "$tmp"
  fi
}

nudge_reset() {
  local key="$1" tmp
  [ -f "$NUDGE_STATE" ] || return 0
  tmp="${NUDGE_STATE}.tmp.$$"
  if jq --arg k "$key" 'del(.[$k])' "$NUDGE_STATE" > "$tmp" 2>/dev/null; then
    mv "$tmp" "$NUDGE_STATE"
  else
    rm -f "$tmp"
  fi
}
