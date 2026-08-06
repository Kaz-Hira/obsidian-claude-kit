#!/usr/bin/env bash
# SessionStart フック: 「今 Vault に溜まっているもの」を最大3行だけ知らせる。
#
# 設計の肝は【平時は何も出さないこと】。閾値を1つも超えていなければ無出力で
# 終わる。additionalContext を出さないので、静かな日は本当に静か。
#
# 背景(2026-07-26):
# この Vault は「生成側は自動・消費側は手動」という非対称を抱えていた。RSS フックが
# 毎セッション Inbox にノートを吐く一方、/triage は10日間走っておらず、Inbox 14件中
# 9件が未処理の RSS ダイジェストだった。溜まっていることを知らせる仕組みが無く、
# 唯一の自動文脈 hot.md は6日古いまま同じ TODO を配り続けていた。
#
# 同時に、通知を足すこと自体が新しい害になりうる。Stop フックの未コミット
# リマインダーは無条件に鳴り続けて誰にも読まれなくなっていた。だからここでは
#   - 最大3行(超過分は「他 n 件」に畳む)
#   - 各行は nudge ライブラリを通す(同じ指摘は1日1回。1.5倍悪化したら即再掲)
#   - 文面は「数字 + 次の1アクション」だけ。解説を書かない
# を守る。
#
# 速度: vault-status.py は実測 0.11s だが、Vault は増えるので
# stale-while-revalidate にしてある(古いキャッシュはそのまま使い、裏で更新)。

set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"
SCRIPTS="${CLAUDE_DIR:-$HOME/.claude}/scripts"
CACHE="$SCRIPTS/.vault-status.json"
CACHE_TTL=600

export VAULT
# shellcheck source=lib/vault-common.sh
. ${CLAUDE_DIR:-$HOME/.claude}/hooks/lib/vault-common.sh
# shellcheck source=lib/vault-nudge.sh
. ${CLAUDE_DIR:-$HOME/.claude}/hooks/lib/vault-nudge.sh

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
vault_scope_check "$cwd" || exit 0

py=$(vault_python) || exit 0

# --- 状態の取得(stale-while-revalidate) ---------------------------------
# 1. 新しいキャッシュがあればそれを使う(ファイルを読むだけ)
# 2. 古ければ【古いまま使い】、裏で再生成を仕掛ける(セッション開始を待たせない)
# 3. 無ければ初回だけ同期生成(timeout 5 で保険をかける)
status=""
if [ -f "$CACHE" ]; then
  age=$(( $(date +%s) - $(stat -f %m "$CACHE" 2>/dev/null || echo 0) ))
  status=$(cat "$CACHE" 2>/dev/null)
  if [ "$age" -ge "$CACHE_TTL" ]; then
    (nohup "$py" "$SCRIPTS/vault-status.py" --refresh >/dev/null 2>&1 &) 2>/dev/null
  fi
else
  status=$(timeout 5 "$py" "$SCRIPTS/vault-status.py" --fresh 2>/dev/null)
fi
[ -n "$status" ] || exit 0

q() { printf '%s' "$status" | jq -r "$1 // empty" 2>/dev/null; }

lines=()
add() { lines+=("$1"); }

# --- 1. 落ちているバックグラウンドジョブ(最優先) -------------------------
# 「気づけないまま毎日失敗する」が今回の元凶なので、これだけは必ず出す
failed_jobs=$(printf '%s' "$status" | jq -r '.jobs | to_entries[] | "\(.key):\(.value.consecutive_failures)"' 2>/dev/null)
if [ -n "$failed_jobs" ]; then
  n=$(printf '%s\n' "$failed_jobs" | wc -l | tr -d ' ')
  names=$(printf '%s\n' "$failed_jobs" | cut -d: -f1 | paste -sd, -)
  if nudge_should_emit "jobs" "$n" 1 1; then
    add "⚠️ バックグラウンドジョブが連続失敗: ${names}。\`tail $SCRIPTS/vault-<job>.log\` を見る(復旧したら \`rm $SCRIPTS/.vault-failures.jsonl\`)"
    nudge_record "jobs" "$n"
  fi
else
  nudge_reset "jobs"
fi

# --- 2. 想起の停滞 ---------------------------------------------------------
# ここだけは「家事」ではなく本来の目的なので、Inbox より上に置く。
#
# 背景(2026-08-04 の実測): Anki に 254 枚あるうち 189 枚(74%)が一度も
# 学習されておらず、復習待ち 65 枚は全部が期限切れだった。レビューがあった日は
# 7/13・7/14・7/29・7/30 の4日だけで、7/30 は試験当日。つまり間隔反復は一度も
# 回っておらず、実態は試験前夜の詰め込みだった。カードを作る側は自動なのに、
# 思い出す側は Anki.app を自分で開かないと始まらない——Inbox と同じ非対称。
#
# 平時 0 になるか: なる。今日 /drill を回せば stale_days=0 で沈黙し、
# 期限切れが 0 枚でも沈黙する。「溜まっている総量(189枚)」は 0 にならないので
# 出さない(警報疲れになる)。出すのは「何日ぶりか」だけ。
study_ok=$(q '.study.available'); study_due=$(q '.study.due'); study_stale=$(q '.study.stale_days')
: "${study_due:=0}" "${study_stale:=0}"
if [ "$study_ok" = "true" ] && [ "$study_due" -ge 5 ] && [ "$study_stale" -ge 3 ]; then
  if nudge_should_emit "study_due" "$study_due" 1 2; then
    add "🧠 想起カード ${study_due}枚が期限切れ(${study_stale}日ぶり)→ \`/drill\`"
    nudge_record "study_due" "$study_due"
  fi
else
  nudge_reset "study_due"
fi

# --- 3. Inbox の滞留 -------------------------------------------------------
inbox_total=$(q '.inbox.total'); inbox_rss=$(q '.inbox.rss'); triage_days=$(q '.triage_days_ago')
: "${inbox_total:=0}" "${inbox_rss:=0}" "${triage_days:=0}"
if [ "$inbox_total" -ge 10 ] || [ "$inbox_rss" -ge 5 ] || [ "$triage_days" -ge 7 ]; then
  if nudge_should_emit "inbox" "$inbox_total" 1 1; then
    add "📥 Inbox ${inbox_total}件(うち未処理 RSS ${inbox_rss}件 / 最終 triage ${triage_days}日前)→ \`/triage\`"
    nudge_record "inbox" "$inbox_total"
  fi
else
  nudge_reset "inbox"
fi

# --- 4. ルート直下の規約外ファイル ------------------------------------------
junk_n=$(printf '%s' "$status" | jq -r '.root_junk | length' 2>/dev/null)
: "${junk_n:=0}"
if [ "$junk_n" -ge 1 ]; then
  junk=$(printf '%s' "$status" | jq -r '.root_junk | join(", ")' 2>/dev/null)
  if nudge_should_emit "root_junk" "$junk_n" 1 3; then
    add "🗂 Vault ルート直下に規約外ファイル ${junk_n}件: ${junk} → 移動または削除"
    nudge_record "root_junk" "$junk_n"
  fi
else
  nudge_reset "root_junk"
fi

# --- 5. lint 重大 ----------------------------------------------------------
# 誤検出を lint-ignore-links で潰して 0 にしてある(2026-07-26)。
# 0 を保てるからこそ「1以上 = 本物」というシグナルになる。
crit=$(q '.lint.critical'); : "${crit:=0}"
if [ "$crit" -ge 1 ]; then
  if nudge_should_emit "lint_critical" "$crit" 1 1; then
    add "🔴 lint 重大 ${crit}件 → \`/lint\`"
    nudge_record "lint_critical" "$crit"
  fi
else
  nudge_reset "lint_critical"
fi

# --- 6. 今週の週次ノート(金・土だけ) ---------------------------------------
# vault-weekly-lazy.sh が自動生成するので通常は出ない。落ちたときの保険。
dow=$(( $(date +%u) % 7 ))   # 0=日 … 6=土
if [ "$dow" -ge 5 ] && [ "$(q '.weekly.exists')" = "false" ]; then
  wk=$(q '.weekly.iso')
  if nudge_should_emit "weekly" 1 1 2; then
    add "🗓 今週の週次ノート(${wk})が未作成 → \`/weekly-review\`"
    nudge_record "weekly" 1
  fi
fi

# --- 7. 今日の日誌(最下位。3行の枠から最初に溢れてよい) ---------------------
# 背景(2026-08-05 の実測): 日誌 28件のうち【1件も】`## 📅 今日の予定` を持って
# いなかった。日誌そのものは Periodic Notes + Templater が確実に作っているので、
# 不足しているのは /daily 固有の価値、つまりカレンダー予定の差し込みだけ。
#
# なぜフックが日誌を【作らない】か: 作ってしまうと Obsidian 側でテンプレが適用
# されなくなり、テンプレ末尾の `tp.user.daily_rss()` が二度と走らない。回っている
# ものを壊す典型なので、ここは知らせるだけに留める(ノートは触らない)。
#
# 平時 0 になるか:
#   - 未作成の行 … Obsidian を開いた日は出ない(直近20日で5日だけ該当)
#   - 予定未挿入の行 … 毎日該当しうるので min_days=3 に緩めてある(3日に1回まで)。
#     それでも鳴りすぎると感じたらこの節ごと消してよい。
# キャッシュ(.vault-status.json)を経由せず直接見る。日付が変われば判定も変わるので、
# 10分の TTL でも古い答えを掴みうるため。ファイル1つの stat と grep なので十分速い。
today_diary="$VAULT/daily/diary/$(date +%F).md"
if [ ! -f "$today_diary" ]; then
  nudge_reset "daily_cal"
  if nudge_should_emit "daily_missing" 1 1 1; then
    add "📔 今日の日誌が未作成 → \`/daily\`"
    nudge_record "daily_missing" 1
  fi
elif ! grep -q '📅 今日の予定' "$today_diary" 2>/dev/null; then
  nudge_reset "daily_missing"
  if nudge_should_emit "daily_cal" 1 1 3; then
    add "📅 今日の日誌にカレンダー予定が未挿入 → \`/daily\`"
    nudge_record "daily_cal" 1
  fi
else
  nudge_reset "daily_missing"
  nudge_reset "daily_cal"
fi

# --- 出力 -------------------------------------------------------------------
[ "${#lines[@]}" -eq 0 ] && exit 0

max=3
body=""
i=0
for l in "${lines[@]}"; do
  i=$((i + 1))
  [ "$i" -gt "$max" ] && break
  body="${body}${l}"$'\n'
done
extra=$(( ${#lines[@]} - max ))
[ "$extra" -gt 0 ] && body="${body}(他 ${extra} 件)"$'\n'

jq -nc --arg c "$(printf '%s' "$body")" \
  '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:("【Vault の溜まり具合】ユーザーが求めたときだけ触れる。勝手に着手しないこと。\n" + $c)}}'
exit 0
