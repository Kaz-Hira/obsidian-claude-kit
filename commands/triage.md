---
description: Inbox を仕分ける(クイックキャプチャの断片と未タグノートを、切り出し・タグ付け・破棄に振る)
allowed-tools: Read, Write, Edit, Grep, Bash(date:*), Bash(uv run:*), Bash(python3:*)
---

Inbox の出口処理。`{{VAULT}}` で、キャプチャ経路に溜まったものを仕分ける。

## 対象

1. **`Inbox/クイックキャプチャ.md` の断片**(`- MM-DD HH:MM …` 行)
2. **Inbox の未タグ独立ノート**(音声書き起こし・defuddle クリップ等。`クイックキャプチャ.md` 自体は除く)

## 手順

1. 対象を全部読む。空なら「仕分けるものなし」で終わる。
2. **対象②(未タグ独立ノート)は、まずローカルモデルの第一候補を取る**:
   `python3 ~/.claude/scripts/vault-autotag.py`(dry-run。**`--apply` は付けない** — triage は自前でタグ付け＆移動するため)。
   出力 `[tag] → 提案  name  「preview」` を叩き台に、各ノートの中身を読んで**検証・修正する**。モデルは「1件1タグ・既存タグ限定・なければ NONE」の制約付きなので、粒度の粗さや取り違え・NONE は Claude が正す。この一次候補を下の処遇案に織り込む。
3. 各項目について処遇案を作る:
   - **切り出し** — 実ノートに育てる価値がある断片。タイトル案・タグ案(`study/○○` `memo/○○` `ref/○○` `sys/○○`。既存タグ体系から選ぶ)・本文の膨らませ方を添える
   - **統合** — 既存ノートに追記すべき内容。意味検索で近いノートを探して指す:
     `uv run --quiet {{CLAUDE_DIR}}/vault-search/vault_search.py search "<断片の要旨>" -k 3`
   - **デイリー行き** — 日記的な一言。その日の daily の Memo 節へ
   - **ブログネタ** — 自分で検証・実装した話に繋がる話題(RSS ダイジェストの項目が主な供給源)。
     `Blog/ブログ管理.md` の `## 💡 ネタ` 列に**1行だけ**足す:
     `- [ ] <ネタ一行> — 出典 [[20260726-RSS]]`
     **ノート化はしない**。この Vault の病理は生成過剰で、ネタごとにノートを作ると
     Inbox 問題を Blog に移すだけになる。カンバンの1行が正しい粒度。育ったら
     `obsidian-blog` スキルで下書きに引き上げる(`status: idea → draft → published`)
   - **破棄** — 用済み・重複
4. **処遇案を一覧で提示し、ユーザーの承認を得る**(まとめて確認。1件ずつ聞かない)。autotag の一次候補と Claude の最終案が食い違ったノートは、その旨を添える。
5. 承認されたものだけ実行する:
   - 切り出し: `template/uniquenote.md` 準拠の frontmatter(`date +%Y%m%d%H%M%S` / `date '+%Y-%m-%d %H:%M:%S'`)で新ノートを作る。**タグに対応するフォルダへ直接置く**(`study/*→Study/` `memo/*→Memo/` `ref/*→Reference/` `sys/*→System/`。Auto Note Mover は Obsidian 起動中しか動かないため、最初から正しい場所に置く)
   - 統合: 既存ノートに追記(見出しの体裁を合わせる)
   - ブログネタ: `Blog/ブログ管理.md` の `## 💡 ネタ` 列に1行追記(ノートは作らない)
   - 処理済みの断片行はバッファから削除。処理済みの独立ノートはタグを付けて移動
6. 結果(作ったノート・移動先・削除した行数)を報告する。関連 MOC への接続が要りそうなら提案する。
7. 最後に、次回の滞留計測のためスタンプを更新する: `date +%s > {{CLAUDE_DIR}}/scripts/.vault-triage-stamp`
   (SessionStart ダッシュボードが「最終 triage n 日前」を出すのに使う)

## 補足

- 保留したい断片は触らずバッファに残す(それが正常。バッファは常設)
- タグは**既存の名前空間から選ぶ**(新設はユーザー確認)。auto-note-mover 契約: `ref/*→Reference` `sys/*→System` `study/*→Study` `memo/*→Memo` `blog/*→Blog`
- autotag(手順2)は gpt-oss:20b を使う([[Inboxタグ自動付与]])。Ollama が落ちていると失敗する — その場合はスキップして Claude が直接タグを判断してよい(結果は変わらない、速度だけの問題)。あくまで一次候補で、最終判断は Claude
- `updated:` はコミット時に pre-commit フックが自動更新するので手で触らなくてよい
