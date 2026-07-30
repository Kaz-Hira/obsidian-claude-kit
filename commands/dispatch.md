---
description: 中規模タスクを分解し、各エンジンの得意分野に割り当てて並列実行→Claude が統合する
argument-hint: "[--code|--research] [--note] [--engines grok,gemini,local,codex] <タスク>"
allowed-tools: Task, Read, Write, WebSearch, WebFetch, Bash(date:*), Bash(agy:*), Bash(grok:*), Bash(codex:*), Bash(ollama:*), Bash(curl:*), Bash(uv run:*)
---

`$ARGUMENTS` のタスクをサブタスクに分解し、エンジン特性に従って割り当て、並列実行して統合する。**分解が要る中規模タスク用**のディスパッチャ。

## 棲み分け(まずここで判定)

- **「Web リサーチ→Inbox ノート化」だけが目的なら `/research` を使う**(重複実装しない。fan-out も `--fan` で持っている)
- 1〜2 ステップで終わる自明な作業なら分解せず直接やる(ディスパッチは起動コストが掛かる)
- /dispatch の出番: 「調査+実装叩き台+レビュー」のように**性質の異なるサブタスクが混ざる**とき、または同種の調査を**複数の切り口に扇形分割**したいとき

## エンジン割当の簡易表

**正本は [[agents_tools_usage]] の「エンジン特性早見表」。食い違ったらノートが正。**

| サブタスクの性質 | 担当 | 呼び出し |
|---|---|---|
| 幅出し・長文の下調べ | Gemini | `cd /tmp && agy [--model "<canonical>"] --print-timeout 9m -p "..."` |
| リアルタイム web / X | Grok | `cd /tmp && grok -p "..."` |
| コード叩き台・別視点レビュー | Codex(ログイン時のみ)→ 代替 Gemini | `cd /tmp && codex exec --skip-git-repo-check "..."` |
| 分類・機械的要約・機密・大量バッチ | Ollama | `ollama run llama3.2 "..."`(精度重視は `gpt-oss:20b`・約66s) |
| コードベース横断調査 | Explore / general-purpose | Task ツール |
| 実装レビュー | code-reviewer / security-reviewer | Task ツール |
| **判断・統合・文体・設計** | **Claude 本体** | 委譲しない |

- **fish 関数(ask-*)は Bash から呼べない(exit 127)。CLI 直叩き・必ず `cd /tmp &&` から**(agy のワークスペース汚染・`.grok/rules` 回避)
- `--engines` 指定があればその範囲に限定する

## 手順

1. **分解**: タスクをサブタスク 3〜6 個に分解する。多すぎるなら分解の粒度が細かすぎる(統合コストが勝つ)。
2. **割当表の提示と承認**: 次の形の表を**1画面で提示し、承認を得てから実行**する:

   | # | サブタスク | 期待成果物 | 担当エンジン | 所要目安 |
   |---|---|---|---|---|

   割当理由が自明でないものは一行添える。承認なしで外部 CLI を走らせない。
3. **並列実行**:
   - 外部 CLI は **`run_in_background` で並列起動**(各1〜数分)。gtimeout/`--print-timeout` は /research の値を踏襲(agy は `--print-timeout 9m`、Bash 側 timeout はそれより長く)
   - Claude 内サブエージェント(Explore / general-purpose / code-reviewer)は **Task ツールで同一メッセージ内 fan-out**
   - 待ち時間に Claude 本体が担当分(判断・統合の準備)を進める
4. **収集・統合**:
   - 取得できなかったエンジンは**黙って落とさず「〜は応答なし」と1行報告**
   - エンジン間で食い違う主張は「Grok は〜、Gemini は〜」と**両論併記→Claude が判断**を明記
   - 外部エンジン由来の URL は /research 手順5と同じ裏取り(`curl -s -o /dev/null -w '%{http_code}' -L --max-time 20 <URL>` で 200 確認。重要な主張は WebFetch で中身も)。**404 は載せず「批判・限界」へ**
5. **出力**:
   - 既定は**セッション内の統合報告**(サブタスク別の要点+統合結論+担当エンジンの明記)
   - `--note` 指定時のみ Inbox ノート化(フォーマットは /research の出力フォーマットに揃える。エンジン欄に割当表を残す)

## コーディング系(--code)の上限

- /dispatch が扱うのは**「叩き台生成+レビュー」まで**。生成物を親ブランチに直接入れない(Claude 本体が統合・検証してから)
- worktree 分離が要る規模(複数エージェントの並行実装・後戻りしにくい変更)は **/dispatch の外**: 軽い並列は `claude-squad`、重い・本番寄りは `h5i env`(propose→apply ゲート)。いずれも TUI なのでユーザーがターミナルで直接起動する([[agents_tools_usage]] ワークフロー2)

## 補足

- **agmsg は組み込まない**: /dispatch は同期の fire-and-collect で完結する。別々に起動した長期セッションとの協調が要るときだけ、統合結果を `/aa` で送る(dispatch の外)
- 外部エンジンの出力は「材料」。そのまま貼らず、Claude が検証・統合してから使う
- 全サブタスクが同一エンジンに割り当たるなら /dispatch の意味が薄い。直接 ask-* / /research を提案する
