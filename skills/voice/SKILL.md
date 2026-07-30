---
name: voice
description: >-
  音声ファイル(授業録音・ボイスメモ等)を mlx-whisper で文字起こしし、Obsidian Vault の
  Inbox/ に独立ノートとして保存する。ユーザーが「この録音をノート化して」「文字起こしして」
  「ボイスメモを取り込んで」「授業の録音をテキストにして」と言ったときや、音声ファイル
  (.m4a .wav .mp3 など)のパスを渡されたときに使う。
allowed-tools: Bash, Read, Edit
---

# voice: 音声 → (要約) → Inbox ノート

実体は `~/.claude/scripts/vault-voice.sh`。ローカルの mlx-whisper(large-v3-turbo)で文字起こしし、続けてローカル Ollama で要約する。音声もテキストもどこにも送信されない。

出力ノートは2段構成:
- `## 要約` — Ollama が生成(要点/決定事項/アクションアイテム/キーワード)。frontmatter の `title` も自動充填される
- `## 文字起こし全文` — mlx-whisper の原文(書き換えない)

## 手順

1. 渡された音声ファイルの存在を確認する
2. モデルキャッシュ(`~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo`)が無ければ、初回は約1.5GBのダウンロードが走る旨を先に伝えて了解を取る
3. 実行する: `~/.claude/scripts/vault-voice.sh <audiofile>`(文字起こし＋要約で時間がかかるのでタイムアウトを長めに)。要約が不要で生テキストだけ欲しいときは `--raw` を付ける
4. 出力された uid のノート `Inbox/<uid>.md` を開いて確認する。`title` が空(短尺・`--raw`・要約失敗)なら内容から1行タイトルを付ける。**`## 文字起こし全文` は原文なので書き換えない**
5. タグ付けと仕分けは /triage の領分。まとまった書き起こしなら /triage を案内する

## 要約段(Stage A)

- 既定モデルは `gpt-oss:20b`(品質重視)。速度優先や短尺は環境変数 `VAULT_VOICE_SUM_MODEL=llama3.2` で切替
- ハング保険に `timeout`(既定300s, `VAULT_VOICE_SUM_TIMEOUT`)で囲む。要約が空でも生テキストで必ずノートは作る
- `VAULT_VOICE_SUM_MINCHARS`(既定200)未満の短い転写は要約せず生のまま
- 要約は**ローカルにオフロード**(分業規約。Claude は使わない)。Ollama がハングしたら `brew services restart ollama`(→ [[Ollama常駐設定]])

## マイク録音(rec)は本人操作

`rec` モードは Ctrl-C で止める対話型なので、Claude からは起動しない。録音したいと言われたら、ターミナルでの実行を案内する:

```bash
~/.claude/scripts/vault-voice.sh rec
```

## 落とし穴

- 文字起こしは日本語固定(`--language ja`)。英語音声を渡されたら精度が落ちる旨を伝える
- 書き起こし結果が空のときはノートが作られない(スクリプトが exit 1)。無音ファイルや形式非対応が疑われる
- 話者分離(誰の発言か)はまだ無い。単一話者(授業)向け。複数話者ミーティングで話者ラベルが要るなら Stage B(pyannote)を別途入れる話になる
