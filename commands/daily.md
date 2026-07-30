---
description: 今日のデイリーノート(daily/diary/YYYY-MM-DD.md)をテンプレから生成し、Googleカレンダーの予定を差し込む
allowed-tools: Bash(date:*), Read, Write, Edit
---

`daily/diary/<日付>.md` を Templater テンプレ(`template/templater/dailynote_temp.md`)の**解決済みの姿**で作る。Obsidian を開かなくても CLI から今日のノートを起こせる。テンプレ単体では届かない **Google カレンダーの今日の予定**を `## 📅 今日の予定` 節として差し込むのがこのコマンドの付加価値。

対象日は `$ARGUMENTS`(`YYYY-MM-DD`)。空なら今日。

## 手順

1. **日付フィールドを算出**する(macOS BSD date。python 不要):
   ```bash
   D="${ARGUMENTS:-$(date +%F)}"
   Y=${D:0:4}; ym=$(date -j -f %F "$D" +%Y-%m)
   mmdd=$(date -j -f %F "$D" +%m%d); ymd=$(date -j -f %F "$D" +%Y%m%d)
   yst=$(date -j -v-1d -f %F "$D" +%F); tmr=$(date -j -v+1d -f %F "$D" +%F)
   lasty=$(date -j -v-1y -f %F "$D" +%F); wd=$(date -j -f %F "$D" +%u)
   rem=$(( ($(date -j -f "%Y-%m-%d" "$Y-12-31" +%s) - $(date -j -f "%Y-%m-%d" "$D" +%s)) / 86400 ))
   echo "$D $Y $ym $mmdd $ymd $yst $tmr $lasty wd=$wd rem=$rem"
   ```
   曜日は `wd`(1=月〜7=日)を `月火水木金土日` に対応させる。
2. **既存チェック**。`daily/diary/<D>.md` が既にあれば**上書きしない**。中身を Read し、`## 📅 今日の予定` 節が無ければ手順4の予定だけを Edit で差し込む。あれば「既に存在」と報告して終わる。
3. **無ければテンプレを解決して Write** する。下の骨組みの `{{…}}` を手順1の値で埋める(` ```tasks ` ブロックは Obsidian Tasks プラグインのクエリなので**一字一句そのまま**残す):

   ````markdown
   ---
   aliases:
     - "{{mmdd}}"
     - "{{ymd}}"
   tags:
     - "daily"
     - "{{Y}}"
     - "{{ym}}"
   ---
   #### << [[{{yst}}|Yesterday]] | [[{{lasty}}|Last year]] | [[{{tmr}}|Tomorrow]] >>

   今年の残り日数：{{rem}}日

   -----------------------------------

   ## Diary：{{D}} ({{曜日}})

   -

   ## Memo

   -

   ## 📅 今日の予定

   {{カレンダー予定 — 手順4}}

   ## ✅ 今日のタスク

   ```tasks
   not done
   due before tomorrow
   ```

   ## 📥 期日なしタスク

   ```tasks
   not done
   no due date
   limit 10
   ```
   ````

4. **カレンダー予定を取得**する。Google カレンダー MCP の `list_events` で対象日(00:00〜23:59)の予定を引く。ツールが未ロードなら ToolSearch で `calendar list_events` を引いてから呼ぶ。各予定を `- HH:MM–HH:MM タイトル`(終日は `- 終日 タイトル`)の箇条書きにする。予定ゼロなら `- (予定なし)`。**MCP が未接続・認証切れなら 📅 節は `- (カレンダー未取得)` にして、認証は本人操作である旨を一言添える**(勝手にリトライしない)。
5. 生成/更新したノートのパスを報告し、Obsidian で開いて Diary/Memo を埋めるよう促す。

## 落とし穴

- **Templater の `<% %>` を残さない**。このコマンドは Templater を通さないので、日付・曜日・残り日数はすべて手順1で**確定値**にして書く。`<%* await tp.user.daily_rss() %>` は複製しない(RSS は SessionStart フックの領分)。
- ファイル名は `daily/diary/YYYY-MM-DD.md`。`daily/` 直下ではなく `diary/` サブフォルダに置く(weekly/monthly と分かれている)。
- タスククエリ・見出しの絵文字・区切り線は既存ノート(例: `daily/diary/2026-07-21.md`)と揃える。勝手に構成を変えない。
- `2/29` の翌年計算で BSD date は `3/1` に丸める。閏日だけの些細な差なので気にしない。
