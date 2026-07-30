---
description: Study/ のノートから Anki デッキ(.apkg)を生成する
allowed-tools: Bash(uv run:*), Read
---

`Study/` のノートを Anki カードに変換する。仕組みの詳細は [[Anki生成]]。

## 手順

1. `$ARGUMENTS` を見て対象を決める:
   - `study/○○` のようなタグなら `--tag study/○○`
   - `--all` ならそのまま全 Study ノート
   - `.md` を含むならファイルパスとして渡す
   - 空なら、どの科目を出すか(またはノート)をユーザーに一度聞く
2. 生成する(**既定で `--llm`**。能動的想起カードにする):
   ```
   uv run --quiet {{CLAUDE_DIR}}/scripts/vault-anki.py <対象> --llm
   ```
   - gpt-oss が遅いので `--all --llm` は15分程度かかる。初回は待つ旨を先に伝え、長ければバックグラウンド実行を検討
   - Ollama が停止していると失敗する。その場合は `brew services start ollama`
3. 出力(枚数・デッキ・.apkg のパス)を伝える。既定の出力先は `~/Downloads/`。
4. **ダブルクリックで Anki に取り込む**ことを一言添える。Anki 未導入なら `brew install --cask anki` を案内する。

## 補足

- `--llm`: gpt-oss:20b が各ノートを「質問→答え」のカードに変換(学習に使えるのはこちら)。詳細は [[Anki生成]]
- **ノート単位でキャッシュ**するので、2回目以降は変更したノートだけ LLM を呼ぶ(速い・guid 安定で重複なし)
- `--llm` を外すと高速な見出し分割モード(下見用。想起には向かない)
- 数式は Anki の MathJax で描画される
