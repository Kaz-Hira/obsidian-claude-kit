#!/usr/bin/env bash
# Vault フック共通ライブラリ。source して使う(実行しない)。
#
# 提供するもの:
#   vault_scope_check <cwd>          — cwd が Vault 配下か判定(4フックで重複していた6行)
#   vault_python                     — python3 >= 3.10 の絶対パスを返す
#   vault_bg_run <job> <log> <cmd…>  — 成功時のみスタンプを進めるバックグラウンド実行
#
# 背景(2026-07-26):
# RSS ジョブが 7/24 から毎日サイレントで落ちていた。原因は2つが重なったもの:
#   1. フックが `/usr/bin/python3`(3.9.6)を絶対パスで指定していた。直前に
#      PATH を export しているのに、絶対パスがそれを無効化していた。
#      vault-rss.py:79 の `-> bytes | None`(PEP 604 / 3.10+)が def 実行時に TypeError。
#   2. `touch $STAMP` を実行【前】に行っていたため、失敗してもスタンプが進み、
#      GUARD 窓が閉じて再試行されず、ログを読まない限り永久に気づけなかった。
# このライブラリは 1 を vault_python、2 を vault_bg_run で構造的に潰す。
#
# 注意: py_compile / ast.parse ではこの種のバグを検出できない。PEP 604 は
# アノテーション評価時(= def 実行時)に落ちるので構文チェックは通ってしまう。
# 検証は必ず「実際に走らせる」こと。

# ---------------------------------------------------------------------------
# cwd が Vault 配下かを判定する。配下でなければ 1 を返す。
#   使い方: vault_scope_check "$cwd" || exit 0
# 実パス(pwd -P)で比較するので、symlink 経由の cwd でも正しく判定する。
# ---------------------------------------------------------------------------
vault_scope_check() {
  local cwd="$1" vault_real
  [ -n "$cwd" ] || return 1
  cwd=$(cd "$cwd" 2>/dev/null && pwd -P) || return 1
  vault_real=$(cd "${VAULT:-$HOME/Documents/Obsidian_Vault}" 2>/dev/null && pwd -P) || return 1
  case "$cwd/" in
    "$vault_real"/*) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------------------
# python3 >= 3.10 の絶対パスを stdout に出す。見つからなければ 1 を返す。
# Homebrew を最優先し、PATH 上の python3、最後に /usr/bin/python3 の順に試す。
# 各候補は「実際にバージョンを聞いて」判定する(パスから推測しない)。
# ---------------------------------------------------------------------------
vault_python() {
  local c
  for c in /opt/homebrew/bin/python3 "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# バックグラウンドジョブを「成功したときだけ成功を記録する」形で起動する。
#
#   vault_bg_run <job名> <ログパス> <コマンド…>
#
# 状態ファイルを3つに分ける(1つのスタンプに排他ロックと成功時刻を兼ねさせない):
#   .vault-<job>-lock       二重起動防止のみ。起動【前】に touch
#   .vault-<job>-stamp      最終【成功】時刻。子プロセスが exit 0 のときだけ
#   .vault-failures.jsonl   失敗の記録。ダッシュボード(vault-status.py)が読む
#
# 返り値: 0=起動した / 1=ロック中で見送り / 2=連続失敗で停止中 / 3=python等が無い
#
# 連続失敗が VAULT_FAIL_LIMIT(既定3)に達したジョブは起動しない(サーキット
# ブレーカ)。無限に失敗し続けるより、ダッシュボードに1行出して人に見せる方がよい。
# 復旧したら .vault-failures.jsonl から当該 job の行を消す(または全消し)。
# ---------------------------------------------------------------------------
vault_bg_run() {
  local job="$1" log="$2"; shift 2
  local dir="${VAULT_STATE_DIR:-$HOME/.claude/scripts}"
  local lock="$dir/.vault-${job}-lock"
  local stamp="$dir/.vault-${job}-stamp"
  local faillog="$dir/.vault-failures.jsonl"
  local guard="${VAULT_GUARD:-900}"
  local limit="${VAULT_FAIL_LIMIT:-3}"

  # ロック中なら見送る(BG 実行が終わって成果物を書くまでの窓を保護)
  if [ -f "$lock" ]; then
    local age=$(( $(date +%s) - $(stat -f %m "$lock" 2>/dev/null || echo 0) ))
    [ "$age" -lt "$guard" ] && return 1
  fi

  # サーキットブレーカ: このジョブが連続 $limit 回失敗していたら起動しない。
  # 成功時に当該ジョブの行を消すので、残っている行数 = 連続失敗回数。
  # grep -c は不一致のとき "0" を出して exit 1 なので、|| で握って 0 に倒す。
  if [ -f "$faillog" ]; then
    local recent
    recent=$(grep -c "\"job\":\"$job\"" "$faillog" 2>/dev/null) || recent=0
    [ "${recent:-0}" -ge "$limit" ] && return 2
  fi

  touch "$lock"

  # 成否の判定は子プロセス【自身】が行う。親(フック)はここで待たない。
  #   成功: stamp を更新し、このジョブの失敗履歴を消す(ブレーカを戻す)
  #   失敗: failures.jsonl に1行追記する。stamp は【進めない】ので次回再試行される
  # 注意: `cmd & ; ( wait $! ) &` は動かない。wait は自分の子にしか使えず、
  # サブシェルから見てコマンドは兄弟なので、必ずサブシェルの【中で】起動して待つ。
  (
    trap '' HUP INT
    # Templater 経由の起動は「ノートが書かれる瞬間」に走るので、Obsidian が
    # ファイルを書き終えるまで待つ必要がある(旧 data.json の sleep 3 / sleep 8)。
    # 子プロセスの中で待つので、フック側・Templater 側とも待たされない。
    [ "${VAULT_BG_DELAY:-0}" -gt 0 ] 2>/dev/null && sleep "$VAULT_BG_DELAY"
    if "$@" >>"$log" 2>&1; then
      date +%s > "$stamp"
      if [ -f "$faillog" ]; then
        grep -v "\"job\":\"$job\"" "$faillog" > "$faillog.tmp" 2>/dev/null || : > "$faillog.tmp"
        mv "$faillog.tmp" "$faillog"
      fi
    else
      rc=$?
      tail_line=$(tail -n 1 "$log" 2>/dev/null | tr -d '"\\' | tr '\n' ' ' | cut -c1-200)
      printf '{"ts":"%s","job":"%s","rc":%s,"tail":"%s"}\n' \
        "$(date +%FT%T)" "$job" "$rc" "$tail_line" >> "$faillog"
    fi
  ) >/dev/null 2>&1 &
  disown 2>/dev/null || true
  return 0
}
