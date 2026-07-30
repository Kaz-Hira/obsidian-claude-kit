# Vault 側の規約(`<vault>/CLAUDE.md`)

このキットのコマンドは、**Vault のルートに置いた `CLAUDE.md`** を読んで
「このノートをどこに置くか」「どのタグを付けるか」を判断します。
ここが空だと `/save` も `/triage` も置き場所を決められません。

Claude Code は毎セッションこのファイルを自動で読むので、**500語以内に保ち、
詳細は Vault 内の別ノートへのリンクで指す**のが要点です。長いほど毎ターン課金され、
かつ守られなくなります。

## 雛形

以下をコピーして `<vault>/CLAUDE.md` に置き、自分の運用に書き換えてください。

````markdown
# Vault 規約

毎セッション自動ロードされる。**500語以内を維持**し、詳細はポインタ先に置く。

## 文脈の扱い(最重要)

- **hot.md は SessionStart フックで注入済み。再 Read しない**(二重課税)
- 横断調査・多ファイル読みはサブエージェントに委譲し、**結論だけ**受け取る
- 要約・下調べなどかさばる下処理は外部 CLI にオフロードする

## フォルダ=種類

| フォルダ | 種類 | タグ |
|---|---|---|
| `Reference/` | 外部知識(恒久) | `ref/*` |
| `System/` | 自作システムの取説 | `sys/*` |
| `Study/` | 学習ノート | `study/*` |
| `Memo/` | 個人メモ | — |
| `Blog/` | 公開記事 | — |
| `Inbox/` | 未仕分け(/triage 対象) | — |
| `daily/` | 日記・週次 | `#daily` |

- **直交タグ**(フォルダを決めない横断タグ): `decision` = 決定録 / `clip` = 未仕分けクリップ /
  `project` `dashboard` `moc`
- 新規ノートの frontmatter は `template/uniquenote.md` 準拠(uid / created / updated / tags)
- `/research` の成果は `researched` / `engine` を、クリップは `source_url` / `fetched` を足す

## 不変ルール

- ブログ本文の執筆・推敲はオフロード禁止(文体が資産)
- トークン節約の動機は金銭でなく**速度と文脈汚染防止**
````

## なぜこの形なのか

**フォルダは「種類」で切り、「話題」で切らない。** 話題で切ると必ず二重帰属が起きて
(「AI の学習ノート」は `AI/` か `Study/` か)、置き場所の判断が毎回発生します。
種類なら一意に決まります。話題の横断は MOC とタグに任せます。

**タグはフォルダに従属させる。** `ref/*` が付いたノートは `Reference/` にある、という
1対1の対応にしておくと、[Auto Note Mover](https://github.com/farux/obsidian-auto-note-mover)
で自動的に移動できます。`vault-folder-guard.sh` はこの対応が崩れる書き込みを止めます。

**直交タグは「フォルダを決めないもの」に限る。** `decision`(決定録)はどのフォルダにも
出現しうるので、フォルダを決めるタグとは別系統にします。auto-note-mover の規則に
一致しないタグはノートを動かさないので、この2種は共存できます。

## `Inbox/` の扱い

クリップは `Inbox/` に入り、タグは `clip` **単体**にします。
`ref/` で始まるタグを付けると auto-note-mover が即座に `Reference/` へ動かしてしまい、
仮置きになりません。

読み終えたら `Reference/` に昇格させるか消す。**`Inbox/` は溜める場所ではありません。**
`/triage` はこの前提で動き、ダッシュボードは滞留日数を見て通知します。

## テンプレート

`<vault>/template/uniquenote.md`(Templater 前提):

```markdown
---
title: <% tp.file.title %>
aliases:
uid: "<% tp.date.now('YYYYMMDDHHmmss') %>"
created: <% tp.date.now('YYYY-MM-DD HH:mm:ss') %>
updated: <% tp.date.now('YYYY-MM-DD HH:mm:ss') %>
tags:
---
```

`updated` は pre-commit フックで自動更新するのが確実です(手で直すと必ず忘れます)。

## LLM に常時読ませる範囲を絞る

Vault 全体を文脈に入れると破産します。frontmatter に `ai: false` を書いたノートを
索引から除外し、「常時知識」は `Reference/` + `System/` 程度に留めるのが実用的でした。
構造ページ(MOC、ダッシュボード、`hot.md` 自身)は基本的に `ai: false` です。
