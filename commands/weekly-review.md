---
description: 今週の週次ノートを開き、Vault の健全性・統合候補・構造レビュー・Inbox 消費を1回で片づける
allowed-tools: Read, Edit, Glob, Grep, Task, Bash(date:*), Bash(python3 {{CLAUDE_DIR}}/scripts/vault-weekly.py:*), Bash(python3 {{CLAUDE_DIR}}/scripts/vault-moc-audit.py:*), Bash(python3 {{CLAUDE_DIR}}/scripts/vault-lint.py:*), Bash(git -C {{VAULT}} status:*), Bash(git -C {{VAULT}} diff:*)
---

週次レビューの器を開き、**溜まっているものをまとめて消費する**。

この Vault の病理は「生成側は自動・消費側は手動」だった。`/triage`・`note-synthesizer`・
`vault-structure-reviewer`・`moc-audit`・`/suggest-links` は品質ではなく
**起動を思い出す機会が無いこと**が理由で使われていなかった。それらを週1回の
1コマンドに束ねるのがこのコマンドの役割。

## 手順

### 1. ダイジェストを最新化する

```
python3 {{CLAUDE_DIR}}/scripts/vault-weekly.py
```

今週の週次ノート(`daily/weekly/YYYY-Www.md`)が無ければテンプレごと作られ、
あればダイジェストブロックだけが冪等に置き換わる。**手書き部分は消えない**。

週番号は moment の locale week(日曜始まり)。`--print-week` で確認できる。

### 2. ダイジェストを読む

生成されたノートを Read し、数字を把握する。ここに出るのは
健全性 / トークン使用量 / 今週の新規・更新ノート / Inbox 滞留 / MOC 未収録 /
接続提案 / タグ提案。

### 3. サブエージェント2本を**並列で**起動する

1つのメッセージ内で両方を Task 起動すること(依存が無いので直列にしない)。

- **note-synthesizer** — スコープは**今週いちばん変更が多かったフォルダ1つ**に絞る。
  エージェント定義自身が「全ノート総当たりは避ける」と書いている。ダイジェストの
  「今週の新規/更新ノート」がそのままスコープ指定になる。
  返してほしいもの: 統合候補(どのノートとどのノートが同じ論点を扱っているか)。
- **vault-structure-reviewer** — スコープは今週の diff。配置・タグ・リンクの整合性を
  「フォルダ=種類」表に照らして判定させる。

どちらにも「**結論だけ**返す。ファイルの生の列挙は不要」と明示する。

### 4. 結論を専用ブロックに書く

両者の結論を、週次ノートの

```
<!-- vault-weekly:ai:start -->
<!-- vault-weekly:ai:end -->
```

に書く(無ければダイジェストブロックの**後ろ**に新設する)。

`vault-weekly:start/end` とは**別のブロック**にすること。前者は python が毎回
上書きするので、そこに書いた判断は次回の実行で消える。機械生成と AI の判断を
混ぜない。

書くのは3〜7行。一覧ではなく「何をすべきか」。

### 5. Inbox を消費する

Inbox の中身を確認し、`/triage` の処遇(切り出し / 統合 / デイリー行き / 破棄 /
ブログネタ)に振り分ける案を出す。**ユーザーの承認を得てから**実行する。

処理し終えたら、次回の計測用にスタンプを更新する:

```
date +%s > {{CLAUDE_DIR}}/scripts/.vault-triage-stamp
```

## 原則

- **判断はユーザーに返す**。ノートの削除・移動・統合は承認を得てから
- ダイジェストの数字は機械検出。正否は `/lint` と本人の目で決める
- このコマンド自体が長くなりすぎたら、それは消費しきれていない兆候。
  週次でやることを増やすより、溜まる量を減らす方を考える

引数: **$ARGUMENTS**(フォルダ名を渡すと note-synthesizer のスコープをそれに固定する)
