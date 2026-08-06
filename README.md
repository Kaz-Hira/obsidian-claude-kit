# obsidian-claude-kit

[![verify](https://github.com/Kaz-Hira/obsidian-claude-kit/actions/workflows/verify.yml/badge.svg)](https://github.com/Kaz-Hira/obsidian-claude-kit/actions/workflows/verify.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey)

**Obsidian Vault を Claude Code で運用するための、実際に毎日動いている一式。**
スラッシュコマンド10 / フック9 / スキル9 / サブエージェント5 / スクリプト12。

デモ用に書き起こしたものではなく、個人の Vault(ノート約180本)で回している設定を
そのまま公開用にサニタイズしたものです。同期は [`tools/sync-from-source.py`](tools/sync-from-source.py) が行い、
個人パスの置換漏れが1つでもあれば異常終了します。

---

## これが解く問題

Obsidian と AI を組み合わせると、たいてい**生成過剰・消費不足**になります。
ノートは増える、クリップは溜まる、でも読み返されず、リンクは張られず、
Inbox は永久に空にならない。足りないのはノートを作る道具ではなく、
**作ったものが腐らないようにする側の仕組み**です。

このキットの中身は、ほぼ全部そちら側に寄っています。

| やること | どう自動化されるか |
|---|---|
| 前回の文脈を思い出す | SessionStart フックが `hot.md` を黙って注入する |
| 規約違反に気づく | ファイル編集の直後に PostToolUse が lint を回す |
| 溜まったものに気づく | 起動時にダッシュボードが最大3行だけ知らせる |
| 週次の棚卸し | 週次ノートが自動で用意され、`/weekly-review` が一括で片づける |
| 情報収集 | RSS をローカル LLM で要約して Inbox に落とす |
| 孤立ノートの救出 | 埋め込み検索が意味の近い既存ノートを提案する |

**能動的に叩くコマンドは実質 `/triage` と `/weekly-review` の2つだけ**になります。
残りはフックが勝手に走ります。

## 動いている様子

`/lint`(= `scripts/vault-lint.py`)を、規約違反をわざと仕込んだ小さな Vault に当てた実際の出力:

```console
$ VAULT=~/demo-vault python3 scripts/vault-lint.py
Vault: ~/demo-vault
ノート 4 件 / 添付 0 件
重大 2 / 警告 3 / 提案 5

────────────────────────────────────────────────────────────
重大 (2)
────────────────────────────────────────────────────────────
  リンク切れ  Reference/色管理の基礎.md  →  [[ACEScgの選定理由]]
  タグ契約違反  Study/微分方程式.md  →  #study 系のタグが無く、auto-note-mover が再配置できない

────────────────────────────────────────────────────────────
警告 (3)
────────────────────────────────────────────────────────────
  frontmatter 欠落  Inbox/クリップ.md  →  title, uid, created, updated, tags
  frontmatter 欠落  Study/微分方程式.md  →  tags
  ai 規約  MOC/Reference MOC.md  →  MOC は ai: false のはずが ai: 未設定

────────────────────────────────────────────────────────────
提案 (5)
────────────────────────────────────────────────────────────
  孤立  MOC/Reference MOC.md  →  どこからもリンクされていない
  孤立  Study/微分方程式.md  →  どこからもリンクされていない
  内容が薄い  MOC/Reference MOC.md  →  本文 18 文字
  内容が薄い  Reference/色管理の基礎.md  →  本文 31 文字
```

重大・警告・提案の三段に分かれているのが肝で、**提案は無視してよい**。
全部を同じ強さで出すと、結局どれも読まれなくなる。

これは `/lint` として手で叩くこともできるが、実際には
PostToolUse フックがファイル編集の**直後**に自動で走る。
規約は、破った瞬間に指摘されないと守られない。

## 入っているもの

### スラッシュコマンド

| コマンド | 用途 |
|---|---|
| `/save` | このセッションの知見を、Vault の規約に沿ったノートとして保存する |
| `/triage` | Inbox を仕分ける(切り出し・タグ付け・破棄に振る) |
| `/lint` | 健全性を検査する(リンク切れ・孤立ノート・規約違反) |
| `/weekly-review` | 週次ノートを開き、健全性・統合候補・構造レビュー・Inbox 消費を一括で |
| `/suggest-links` | 孤立ノートに、意味の近い既存ノートへのリンク候補を出す |
| `/research` | テーマを Web リサーチし、出典付きで Inbox ノート化する |
| `/hot` | ホットキャッシュ(`hot.md`)を今のセッションの内容で書き換える |
| `/daily` | デイリーノートを生成する(Google カレンダーの予定を差し込む) |
| `/anki` | `Study/` のノートから Anki デッキ(.apkg)を作る |
| `/dispatch` | 中規模タスクを分解し、複数の LLM に割り当てて並列実行→統合する |

### フック

| イベント | フック | すること |
|---|---|---|
| SessionStart | `vault-hot-cache.sh` | `hot.md` を文脈に注入(報告しない) |
| SessionStart | `vault-dashboard.sh` | 滞留・失敗ジョブを最大3行で通知 |
| SessionStart | `vault-rss-lazy.sh` | RSS 収集をバックグラウンドで起動 |
| SessionStart | `vault-weekly-lazy.sh` | 金・土に週次ノートを用意 |
| PostToolUse | `vault-lint-posttool.sh` | 編集直後に lint |
| PostToolUse | `vault-suggest-links-newfile.sh` | 新規ノートにリンク候補を出す |
| PreToolUse | `vault-folder-guard.sh` | 規約外の場所への書き込みを止める |
| PreToolUse | `git-guard.sh` | 破壊的な git 操作(force push / reset --hard)を止める |
| Stop | `vault-stop.sh` | ホットキャッシュの更新を促す |

### サブエージェントとスキル

- **agents** — `note-synthesizer`(重複・分散したノートの統合候補)、`vault-structure-reviewer`(配置とタグの妥当性)、`fact-checker`(公開前の裏取り)、`code-reviewer`、`security-reviewer`
- **skills** — `vsearch`(意味で検索する。sqlite-vec + embeddinggemma)、`defuddle`(記事本文だけ抽出してクリップ)、`obsidian-blog`(執筆・推敲)、`voice`(音声をノート化)、`moc-audit`(MOC の抜け漏れ)、`vault-automation`(フックを自作するときの型)ほか

### Vault から Zenn へ公開する

`vault-zenn-sync.py` は、Vault の `Blog/` を正本として Zenn の記事を生成する。
Obsidian 用の frontmatter(`uid` / `created` / `updated` / `tags`)と Zenn 用の設定を
1枚のノートに同居させ、Zenn が読む項目だけを抜き出して書き出す。

```yaml
---
title: 記事タイトル
uid: "20260805090800"
tags: [blog/obsidian]
status: draft
zenn:                              # このブロックがあるノートだけが対象
  slug: my-article-slug
  emoji: "🗂️"
  type: tech
  topics: ["obsidian", "claudecode"]
  published: false
---
```

```bash
VAULT=~/Documents/Obsidian_Vault ZENN=~/dev/zenn-content \
  python3 scripts/vault-zenn-sync.py --check   # 書かずに差分だけ見る
```

Zenn に弾かれる前に slug・emoji・type・topics 数を検査し、
本文先頭の H1 重複を落とし、`[[wikilink]]` が残っていれば警告する
(Zenn では展開されないため)。`published: true` のノートは
「push すると公開される」と明示的に警告する。

## 導入

```bash
git clone https://github.com/Kaz-Hira/obsidian-claude-kit.git
cd obsidian-claude-kit
./install.sh --vault ~/Documents/YourVault --dry-run   # まず何が起きるか見る
./install.sh --vault ~/Documents/YourVault
```

`install.sh` は既存ファイルを `~/.claude/backups/kit-<日時>/` に退避してから置き、
`settings.json` は既存のキーを残したままマージします。

**元に戻すには**、退避先から書き戻してください。`install.sh` は入れたファイルの一覧を
持たないので、削除ではなく復元で戻す設計です。

```bash
cp -R ~/.claude/backups/kit-<日時>/. ~/.claude/
```

導入後、Vault 側に `CLAUDE.md`(フォルダ構成とタグ規約)を置いてください。
雛形と考え方は [`docs/vault-conventions.md`](docs/vault-conventions.md) にあります。
**ここを書かないと `/save` も `/triage` も置き場所を判断できません。**

### 前提

| | 必須 | 用途 |
|---|---|---|
| macOS (Apple Silicon) | ○ | フックは bash + BSD coreutils 前提 |
| Claude Code | ○ | — |
| Python ≥ 3.10 | ○ | スクリプト全般 |
| [uv](https://docs.astral.sh/uv/) | △ | `/vsearch` `/anki` |
| [Ollama](https://ollama.com/) | △ | 埋め込み検索(`embeddinggemma`)、RSS 要約(`gpt-oss:20b`) |
| mlx-whisper | △ | `voice` スキル |

△ は該当機能を使うときだけ。無くても他は動きます。

**動作確認環境** — macOS 15.6 (Apple Silicon, M4) / Python 3.12 と 3.14 / Claude Code 2.x。
Vault はノート約180本、フックは日常的に稼働しています。Intel Mac と Linux は未確認です。

## 設計上の判断

実際に踏んだ失敗から来ているものだけ書きます。

**成功したときだけスタンプを進める。** RSS 収集が3日間サイレントで落ちていたことがあります。
原因は2つの合わせ技で、(1) フックが `/usr/bin/python3`(3.9)を絶対パスで呼んでいたため
PEP 604 の型注釈で `TypeError`、(2) `touch $STAMP` を実行**前**に行っていたため、
失敗してもガード窓が閉じて再試行されず、ログを読まない限り永久に気づけない。
`vault_bg_run` はロック・成功時刻・失敗記録を3つのファイルに分け、
連続失敗するジョブはサーキットブレーカで止めてダッシュボードに1行出します。

**構文チェックでは足りない。** PEP 604 は def の実行時に落ちるので `py_compile` を通ります。
検証は必ず実際に走らせること。`sync-from-source.py` も生成後に実行検証をします。

**通知は出しすぎない。** 起動時の通知は最大3行、同じことは一定期間再通知しません。
無条件に出すと警報疲れを起こして、本当に見るべき1行が埋もれます。

**Vault の外では黙る。** 全フックが最初に `vault_scope_check` を通り、
cwd が Vault 配下でなければ即座に exit 0 します(symlink 経由でも実パスで判定)。

## これは何ではないか

- **Obsidian プラグインではありません。** Claude Code 側の設定一式です
- **汎用ではありません。** 特定の運用(フォルダ=種類、直交タグ、Zettelkasten 寄りの uid)を前提にしています。合わなければ `docs/vault-conventions.md` から自分用に組み替えてください
- **macOS 専用です。** Linux 対応の PR は歓迎します

## 貢献

このリポジトリは生成物なので、PR を出す場所に少し癖があります。
[CONTRIBUTING.md](CONTRIBUTING.md) を先に読んでください。
変更履歴は [CHANGELOG.md](CHANGELOG.md) にあります。

## ライセンス

MIT — 詳細は [LICENSE](LICENSE)。
