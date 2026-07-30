---
description: テーマを Web リサーチし、出典付き+「この環境への適用」付きで Inbox ノート化する
argument-hint: "[--aimode] [--grok] [--gemini] [--codex] [--fan] [--model <alias>] <テーマ>"
allowed-tools: WebSearch, WebFetch, Write, Read, Bash(date:*), Bash(agy:*), Bash(grok:*), Bash(codex:*), Bash(curl:*), Bash(uv run:*), mcp__Claude_Browser__preview_start, mcp__Claude_Browser__navigate, mcp__Claude_Browser__read_page, mcp__Claude_Browser__computer, mcp__Claude_Browser__preview_list, mcp__Claude_Browser__preview_stop
---

`$ARGUMENTS` のテーマを調べ、`~/Documents/Obsidian_Vault/Inbox/` に**そのまま /triage に流せる形**でリサーチノートを作る。「調べる→Vault の知識になる」を1コマンドで閉じるループの入口。

## DeepResearch の方針(エンジンの使い分け)

**主エンジンは Claude 自身の `WebSearch`/`WebFetch`**(確実に自動起動でき、そのまま「この環境への適用」まで書けるため)。加えて、大テーマでは外部の**エージェント型 CLI をヘッドレスで並列に走らせて幅を出せる**。全て一発呼び出し(web検索オン)で叩ける:

| フラグ | エンジン | 直接コマンド | 得意 |
|---|---|---|---|
| `--aimode` | Google AI Mode(Gemini)| ブラウザ `udm=50` | ★引用付き合成を一発。**主張ごとに実URL**・ログイン不要・数秒。agy の弱点(出典薄)を埋める |
| `--grok` | Grok | `grok -p` | リアルタイム web / X の最新 |
| `--gemini` | Gemini(agy) | `agy -p` | grounded 検索・幅出し |
| `--codex` | GPT(codex) | `codex exec` | 別視点(要 ChatGPT有料+login) |
| `--fan` | 上記3つ全部 | — | 扇形分割→統合(重いテーマ用) |
| `--model <alias>` | `--gemini`/`--fan` の agy のモデルを指定 | — | 既定は agy の TUI 設定(現在 Gemini 3.1 Pro (High)) |

グローバル CLAUDE.md の規律通り、**幅出し(下調べ)は外部に逃がし、統合・判断・文体・「この環境への適用」は Claude が持つ**。フラグ無しなら WebSearch のみで速い。対話専用モード(Antigravity `/plan`、gemini.google.com の非同期 **Deep Research レポート**)は自動化できないので使わない(理由は [[Antigravity CLI DeepResearch]] と [[Antigravityのリサーチ機能とClaude Codeプラグイン(2026)]])。**混同注意**: `--aimode`(下記の `udm=50` Google AI Mode)は Gemini を裏で使う**ヘッドレスで叩ける**別物で、ログイン不要・数秒・引用付き。「Gemini でディープリサーチを呼ぶ」目的にはまずこちらを使う。gemini.google.com の Deep Research(数分かけて長文レポートを吐く方)はログイン必須・非同期で、必要なら本人操作か claude-in-chrome 経由の重い別実装になる。

**agy は幅出し専用と割り切る(2026-07-17 実測)**。`agy -p` は本物の web 検索を叩ける(ライブの GitHub 統計値を正確に返すことを確認済み)が、**出典は薄い** — 実測では具体的な URL は1本だけで、残りは「検索エンジン経由で要約を抽出」、しかも一部 404 を Google Search のサマリーで補完していた。よって **agy の出す URL は必ず裏取りする**(下の手順5)。

## 手順

1. **重複確認**: まず意味検索で既存ノートと重ならないか見る。重なるなら新規でなく追記を提案:
   `uv run --quiet {{CLAUDE_DIR}}/vault-search/vault_search.py search "<テーマの要旨>" -k 5`
2. **多角検索**: `WebSearch` を切り口を変えて3〜5本(定義/最新動向/批判・限界/実装例 など)。US-only の点に留意。
3. **一次資料の確認**: 有力なソースは `WebFetch` で本文を取り、要点を自分の言葉で。出典 URL は必ず控える。
4. **(任意)外部エンジンで幅出し**: `--aimode`/`--grok`/`--gemini`/`--codex`/`--fan` が付いていれば、該当エンジンを走らせる(CLI 系は **`run_in_background` で並列起動**、各1〜数分かかるので待つ間に WebSearch を進める)。出そろったら統合し、**エンジン間で食い違う主張は「Grokは〜、Geminiは〜」と併記**する(扇形分割→統合)。どのエンジンが何を言ったかは出典欄に残す。取得できなかったエンジンは黙って落とさず「〜は応答なし」と一行。
   - **`--aimode`(Google AI Mode / Gemini・推奨。2026-07-21 実地確認)**: これだけ CLI ではなく**ブラウザ MCP** で叩く。ログイン不要・数秒で、**主張ごとに実URLのインライン引用**が付く(agy の「出典薄」を正面から埋めるのが採用理由)。手順:
     1. `mcp__Claude_Browser__preview_start` に `url: https://www.google.com/search?q=<URLエンコード済みクエリ>&udm=50`。返り値が `navOk:false` なら、返った tabId に対し `mcp__Claude_Browser__navigate` で同じ URL を再試行する
     2. `mcp__Claude_Browser__computer` の `wait`(3秒程度)で待つ — **AI Mode は非同期描画**で、直後だと本文が空
     3. `mcp__Claude_Browser__read_page`(`filter: all`)で**回答本文+インライン引用リンク**を取る。**`get_page_text` は CSS しか返さないので使わない**(実地で確認)。引用は `link "... - 関連リンクを表示" href=...` の形で実URLが入っている
     4. 取れた引用 URL はそのまま手順5の裏取りへ回す(実URLなので agy より軽い)。終わったら `preview_list` で process id を得て `preview_stop` で閉じる
     - **フォールバック検知**: リージョン/実験ゲートで AI Mode 枠が出ず通常検索になることがある。その場合は「AIモードは出ず通常検索にフォールバック」と一行残し、WebSearch 側で補う。US-only の留意点は WebSearch と同じ
   - **重要(2026-07-16 実地で判明): `ask-*` は fish 関数で、Bash ツールの sh では動かない(exit 127)。CLI を直接叩く。全て `cd /tmp &&` から**(Vault 配下だと agy/grok が Vault を作業対象と見なし、ノイズと事故の元):
     - Grok: `cd /tmp && grok -p "<プロンプト>"`(web検索オン。Vault 配下だと `.grok/rules` で agmsg が起動しノイズが乗る)
     - Gemini: `cd /tmp && agy [--model "<canonical>"] --print-timeout 9m -p "<リサーチ用プロンプト>"`(下記)
     - Codex: `cd /tmp && codex exec --skip-git-repo-check "<プロンプト>"`(git ディレクトリ外だと信頼ディレクトリ制約で失敗するのでフラグ必須。要 ChatGPT有料+`codex login`)
   - Grok / Codex に渡すプロンプトは「<テーマ>の要点・最新動向・異論を簡潔に」の形でよい
   - **agy に渡すプロンプトは構造化リサーチ契約にする**(ただの Q&A でなく調査として扱わせ、かつ出典の質を自己申告させるため):
     ```
     Conduct a thorough research investigation on the following topic. Look up
     authoritative sources, summarize the current state of knowledge, surface
     disagreements or open questions, and structure the response with clear
     sections (Background, Key findings, Caveats, Sources).

     In Sources, you MUST separate:
       (a) URLs you actually fetched — give the exact URL
       (b) sources you only saw via search-engine summaries — mark them as such
     If web search was unavailable, reply exactly "NO WEB ACCESS" instead of guessing.

     Topic: <テーマ>
     ```
   - **`--model <alias>`**: `agy` はネイティブに `--model` を持つ(settings.json は汚れない。2026-07-17 検証)。alias → canonical に直して渡す。**canonical 文字列は完全一致が必要**:

     | alias | canonical |
     |---|---|
     | `flash` | `Gemini 3.5 Flash (High)` |
     | `flash-low` | `Gemini 3.5 Flash (Low)` |
     | `pro` | `Gemini 3.1 Pro (High)` |
     | `pro-low` | `Gemini 3.1 Pro (Low)` |
     | `sonnet` | `Claude Sonnet 4.6 (Thinking)` |
     | `opus` | `Claude Opus 4.6 (Thinking)` |
     | `gpt-oss` | `GPT-OSS 120B (Medium)` |

     この表が古びたら `agy models` が正となる(ライブ一覧)。`--model` 未指定なら agy の TUI 既定に任せる。目安: 深いテーマは `pro`(実測 約1分50秒)、幅だけ欲しいなら `flash`
   - `--print-timeout` の既定は 5分。Pro での実テーマが約1分50秒だったので通常は足りるが、重いテーマでは `9m` 等に伸ばし、Bash 側の `timeout` はそれより長く取る
5. **出典の裏取り(外部エンジンを使ったなら必須)**: 外部エンジンが挙げた URL は**実在を確認してからノートに載せる**。まとめて叩く:
   ```
   for u in <URL1> <URL2>; do echo "$(curl -s -o /dev/null -w '%{http_code}' -L --max-time 20 "$u")  $u"; done
   ```
   - 200 以外(404 等)の URL は**ノートに載せない**。「〜と主張したが出典URLは404」と批判・限界に一行残す
   - 重要な主張は URL の実在だけでなく `WebFetch` で中身も確認する。**「URL が生きている」と「その URL がその主張を支えている」は別**
   - 外部エンジンが「検索要約のみ(URLなし)」と申告した情報源は、出典欄で**裏取り済みのものと明確に分ける**(下の出力フォーマット参照)
6. **ノート化**: 下の形式で Inbox に書く。タグは付けない(triage で決める)か、明らかなら `ref/ai` 等の案を frontmatter に入れる。
7. **報告**: 作ったノートのパスと、`/triage` で Reference へ送れることを伝える。要点3行も添える。

## 出力フォーマット(既存クリップと揃える)

```
---
title: <テーマを表す簡潔な日本語タイトル>
aliases:
uid: "<date +%Y%m%d%H%M%S>"
created: <date '+%Y-%m-%d %H:%M:%S'>
updated: <同上>
tags:
researched: <date +%Y-%m-%d>
engine: <WebSearch, Google AI Mode, ...>
---
# 🔎 <タイトル>

**調査日**: <日付> / **エンジン**: WebSearch(+ 使ったなら Google AI Mode / Grok / Gemini(モデル名) / Codex)

## 要点
- 3〜6個。事実と、それが確からしいかの温度感

## 詳細
（切り口ごとに小見出し。数字・固有名詞は出典に紐づける）

## 批判・限界
- 鵜呑みにできない点、一次検証されていない主張

## この環境への適用
| 部品 | この環境の対応物 | 状態(実装済み/取り入れ候補/対象外) |
（自作システム—[[System MOC]] の各ツール、グローバル CLAUDE.md の分業規律、TCC 制約 等—に照らす）

## 出典
- [タイトル](URL) — 何を取ったか(裏取り済み: 200 を確認、または WebFetch で本文確認)

### 裏取りできていない情報源
- <エンジン名> が挙げたが URL 無し(検索要約のみ) / URL が 404 だったもの。**主張の重みを下げて扱う**
```

## 補足

- **「この環境への適用」を必ず入れる**。単なる要約ではなく「自分の構成にどう効くか」まで書くのがこのループの価値。書けない(自環境と無関係な)テーマなら、その旨を一行残す
- 出典を捏造しない。WebFetch で取れなかったものは「未確認」と明記
- 長時間になりそうなら、検索の本数を絞って一度出し、深掘りは追加指示で
