#!/usr/bin/env bash
# PreToolUse(Write) フック: Obsidian Vault の「フォルダ=種類」不変条件を発生前に守る。
# 新規 .md を (1) Vault ルート直下 または (2) template/ 配下 に作ろうとしたら確認(ask)する。
# lint(PostToolUse)は事後に frontmatter 欠落を指摘するだけで、置き場所の誤りは止めない。
# stdin から JSON を受け取り、hookSpecificOutput.permissionDecision を返す。
# グローバル設定に置かれるため、Vault 外のパスには一切干渉しない。

VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"

input=$(cat)
path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
[ -z "$path" ] && exit 0

emit() {
  # $1 = allow|deny|ask, $2 = 理由
  jq -nc --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$d,permissionDecisionReason:$r}}'
  exit 0
}

# Vault 内の .md / .base / .canvas を対象にする(それ以外は素通り)。
# .base / .canvas を足したのは 2026-07-26。ルート直下に BASE.base・無題のファイル.base・
# 無題のファイル.canvas が実際に溜まっていたため。ただしそれらは Obsidian の UI が
# 作ったもので Write ツールを経由しないので、このフックでは原理的に止められない。
# GUI 由来の迷子は SessionStart ダッシュボード側(vault-status.py の root_junk)で拾う。
# ここで止めるのは Claude 由来の分だけ、という二段構え。
case "$path" in
  "$VAULT"/*.md|"$VAULT"/*.base|"$VAULT"/*.canvas) ;;
  *) exit 0 ;;
esac

# 既存ファイルへの書き込み(= 編集/上書き)は許可。hot.md・Home.md 等の正規ルートファイルや
# /hot・/daily の再生成をブロックしない。新規作成だけを見る。
[ -e "$path" ] && exit 0

rel="${path#"$VAULT"/}"

# template/ 配下への新規ノート = テンプレ契約に触れる。確認する。
case "$rel" in
  template/*)
    emit ask "template/ 配下に新規ファイル ($rel) を作ろうとしています。テンプレは契約です。意図した追加か確認してください。"
    ;;
esac

# ルート直下(サブフォルダなし)の新規ファイル = フォルダ=種類 違反の疑い。確認する。
case "$rel" in
  */*) : ;;  # サブフォルダあり = 型付きフォルダ。許可
  *.base|*.canvas)
    # .md と違い、ルート直下に置く正当な例(hot.md・Home.md のような)が存在しない
    emit ask "Vault ルート直下に新規 ${rel##*.} ファイル ($rel) を作ろうとしています。ルート直下に置く正規の .base/.canvas はありません(既存の BASE.base・無題のファイル.base は Obsidian の誤操作で残った迷子)。置き場所を確認してください。"
    ;;
  *)
    emit ask "Vault ルート直下に新規ノート ($rel) を作ろうとしています。「フォルダ=種類」規約では型付きフォルダ(Reference/System/Study/Memo/Inbox/daily 等)に置きます。ルート直下でよいか確認してください。"
    ;;
esac

exit 0
