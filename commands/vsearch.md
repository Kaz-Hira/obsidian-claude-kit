---
description: Obsidian Vault を意味で検索する(sqlite-vec + embeddinggemma のセマンティック検索)
allowed-tools: Bash(uv run:*), Read
---

Vault のセマンティック検索。キーワード一致ではなく意味の近さで引くので、言い回しが違っても当たる。

## 手順

1. `$ARGUMENTS` が空なら、何を探したいか聞き返す。
2. まず索引を最新化してから検索する(増分なので変更がなければ一瞬):
   ```
   uv run --quiet {{CLAUDE_DIR}}/vault-search/vault_search.py index
   uv run --quiet {{CLAUDE_DIR}}/vault-search/vault_search.py search "<クエリ>" -k 8
   ```
3. 結果は `[距離] パス › 見出し` + 冒頭スニペットの形式。距離が小さいほど近い。
4. 上位ヒットのうち文脈が要りそうなものは Read で本文を確認してから、ユーザーの質問に答える。単なるヒット一覧の転記で終わらせず、どのノートに何があるかを一文で要約して返す。

## 補足

- クエリは自然文でよい。ユーザーの質問文をほぼそのまま渡すのが基本
- ヒットが的外れなら言い換えて1回だけ再検索する(それでもダメなら Grep にフォールバック)
- 索引対象: Vault 全体から `.trash` / `.obsidian` / `template` / `attachments` と `ai: false` のノートを除いたもの。`Study/` や `daily/` も検索対象に含まれる(AIコンテキストの「常時知識」規約とは別枠)
- 全再構築が必要になったら `index --full`
