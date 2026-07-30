#!/bin/bash
# vault-voice.sh — 音声 → mlx-whisper 文字起こし → (Ollama要約) → Inbox ノート
#
#   vault-voice rec              マイク録音(Ctrl-C か Enter で停止)→ 文字起こし → Inbox
#   vault-voice <audiofile>      既存の音声ファイル(授業録音・ボイスメモ等)を文字起こし → Inbox
#   vault-voice --raw <...>      要約段をスキップし、生の文字起こしだけをノート化(従来動作)
#
# ロードマップ③「口からのキャプチャ経路」。書き起こしは**まとまった1件**なので、
# 断片バッファ(capture-inbox.sh)ではなく自分の独立ノート Inbox/<uid>.md を直接作る。
# 初回実行時は whisper モデル(large-v3-turbo, 約1.5GB)を HuggingFace から取得する。
#
# 要約段(Stage A): 文字起こし後、ローカル Ollama で「タイトル + 要点/決定事項/アクション/
# キーワード」を生成し、本文を「## 要約」→「## 文字起こし全文」の2段にする。分業規約に従い
# 要約は**ローカルにオフロード**(Claude は使わない)。ハング対策に timeout で囲む。

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
VAULT="${VAULT:-$HOME/Documents/Obsidian_Vault}"
MODEL="mlx-community/whisper-large-v3-turbo"
SUM_MODEL="${VAULT_VOICE_SUM_MODEL:-gpt-oss:20b}"   # 要約モデル(既定 gpt-oss:20b, 短尺や高速重視なら llama3.2)
SUM_TIMEOUT="${VAULT_VOICE_SUM_TIMEOUT:-300}"        # 要約のハング保険(秒)
SUM_MINCHARS="${VAULT_VOICE_SUM_MINCHARS:-200}"      # これ未満の転写は要約せず生のまま
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --raw フラグを引数のどこにあっても拾って取り除く
RAW=0
args=()
for a in "$@"; do
  if [ "$a" = "--raw" ]; then RAW=1; else args+=("$a"); fi
done
set -- "${args[@]:-}"

case "${1:-}" in
  "")
    echo "usage: vault-voice [--raw] {rec|<audiofile>}"; exit 1 ;;
  rec)
    file="$TMP/rec.wav"
    echo "🎙  録音中… 終わったら Ctrl-C(または q → Enter)"
    # :0 = 既定マイク。デバイスが違うときは `ffmpeg -f avfoundation -list_devices true -i ""` で確認
    ffmpeg -hide_banner -loglevel error -f avfoundation -i ":0" -ac 1 -ar 16000 "$file" || true
    ;;
  *)
    file="$1"
    [ -f "$file" ] || { echo "ファイルが見つからない: $file"; exit 1; }
    ;;
esac

echo "📝 文字起こし中…"
mlx_whisper "$file" --model "$MODEL" --language ja --output-format txt --output-dir "$TMP" >/dev/null

txt="$(cat "$TMP"/*.txt)"
[ -n "$txt" ] || { echo "文字起こし結果が空。ノートは作らない"; exit 1; }

# --- Stage A: ローカル Ollama で要約 + タイトル生成 ---------------------------
title=""       # frontmatter 用(空なら従来どおり /triage 頼み)
summary=""     # 本文「## 要約」用(空なら要約段を出さない)
nchars=$(printf '%s' "$txt" | wc -m | tr -d ' ')

if [ "$RAW" -eq 0 ] && [ "$nchars" -ge "$SUM_MINCHARS" ]; then
  echo "🧠 要約中… ($SUM_MODEL, ローカル)"
  read -r -d '' prompt <<'PROMPT' || true
次は授業/会議などの音声書き起こしです。日本語で構造化してください。出力は厳密に次の形式:

- 1行目: 内容を表す20字程度のタイトルだけ(接頭辞・記号・引用符・「タイトル:」などを付けない)
- 2行目以降: 以下の見出しで要約(該当情報が無い見出しは本文を「特になし」と書く)

### 要点
### 決定事項・結論
### アクションアイテム
### キーワード

前置き・後書き・コードブロックは書かない。ここから本文:
PROMPT
  # プロンプト + 本文を stdin で渡す。timeout でハングを保険(Ollama常駐設定 参照)
  # --hidethinking: gpt-oss 等の推論モデルが吐く "Thinking..." ブロックを抑止(非思考モデルでは無害)
  out="$(printf '%s\n\n%s\n' "$prompt" "$txt" | timeout "$SUM_TIMEOUT" ollama run --hidethinking "$SUM_MODEL" 2>/dev/null || true)"
  # 万一 stdout に混じった ANSI 制御コードを除去(モデル/端末判定次第で漏れることがある)
  out="$(printf '%s' "$out" | LC_ALL=C sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g')"
  if [ -n "$out" ]; then
    # 1行目 = タイトル。YAML を壊す文字(コロン・引用符・先頭記号)を落として1行に整える
    title="$(printf '%s' "$out" | sed -n '1p' | sed -E 's/^[[:space:]#>*"'"'"'-]+//; s/[:"]//g; s/[[:space:]]+$//')"
    summary="$(printf '%s' "$out" | tail -n +2 | sed -e '/./,$!d')"  # 先頭の空行だけ落とす(移植性のある定番)
  else
    echo "⚠️  要約が空(タイムアウト/失敗)。生の文字起こしだけで保存する"
  fi
fi

# --- ノート生成(template/uniquenote.md 準拠) --------------------------------
uid="$(date +%Y%m%d%H%M%S)"; note="$VAULT/Inbox/$uid.md"
while [ -e "$note" ]; do sleep 1; uid="$(date +%Y%m%d%H%M%S)"; note="$VAULT/Inbox/$uid.md"; done
ts="$(date '+%Y-%m-%d %H:%M:%S')"

# printf で組み立て(heredoc の変数展開・命令置換を避け、書き起こし内の $(...) を無害化)
{
  printf '%s\n' '---'
  printf 'title: %s\n' "$title"
  printf '%s\n' 'aliases:'
  printf 'uid: "%s"\n' "$uid"
  printf 'created: "%s"\n' "$ts"
  printf 'updated: "%s"\n' "$ts"
  printf '%s\n' 'tags:'
  printf '%s\n' '---'
  if [ -n "$summary" ]; then
    printf '## 要約\n\n%s\n\n' "$summary"
    printf '## 文字起こし全文\n\n%s\n' "$txt"
  else
    printf '%s\n' "$txt"
  fi
} > "$note"

if [ -n "$title" ]; then
  echo "📥 Inbox に保存しました ($uid) — 要約付き「$title」"
else
  echo "📥 Inbox に保存しました ($uid)"
fi
