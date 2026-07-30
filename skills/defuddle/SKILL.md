---
name: defuddle
description: >-
  Webページから広告・ナビゲーション・フッタを剥がし、記事本文だけをそのままの文章で取り出す。
  要約を経由しないので、引用・事実確認・ブログ執筆の下調べに使える。取得した記事は
  Obsidian Vault の Inbox/ に保存できる。ユーザーが「この記事を取り込んで」「URLの中身を読んで」
  「記事を保存して」「クリップして」「本文だけ抜き出して」と言ったとき、またブログ執筆で
  外部記事を参照するときに使う。
allowed-tools: Bash, Read, Write, Edit
---

# defuddle: Web記事の本文抽出

`defuddle-cli`(Obsidian の作者 kepano による)は、HTML から記事本文だけを取り出す。
広告・ナビ・関連記事・クッキーバナーが落ちる。

## WebFetch との使い分け

**これが最も重要な区別。**

| | 返ってくるもの |
|---|---|
| `WebFetch` | 小型モデルがプロンプトに答えた**要約・回答** |
| `defuddle` | 記事本文**そのまま**の Markdown |

「この記事は何を言っているか」を知りたいだけなら `WebFetch` が速い。
**引用する・事実を確認する・記事を保存する**なら `defuddle` を使う。要約は原文を復元できない。

## コマンド

```bash
defuddle parse <URL または HTMLファイル> [オプション]
```

| オプション | 意味 |
|---|---|
| `--md` | Markdown に変換(付けないと HTML が出る) |
| `-o <file>` | ファイルに書き出す(既定は標準出力) |
| `-p <name>` | プロパティを1つだけ取り出す |
| `-j` | メタデータ込みの JSON |

`--json` で取れるキー: `title` `description` `domain` `author` `published` `site` `image` `favicon` `wordCount` `parseTime` `content` `metaTags` `schemaOrgData`

```bash
defuddle parse "https://example.com/article" --md      # 本文
defuddle parse "https://example.com/article" -p title  # タイトルだけ
```

## 落とし穴

**一時的な失敗がある。** `Error loading content:` が出ても、多くは通信の一時障害。**同じURLで1回リトライする**。2回続けて失敗したら次の回避策へ。

**回避策 — curl で落としてから食わせる。** 直URLで取れないサイトでも、これならほぼ通る。

```bash
curl -sL -m 20 "$URL" -o /tmp/page.html && defuddle parse /tmp/page.html --md
```

**手書きのローカルHTMLは `<meta charset="utf-8">` が要る。** 無いと日本語が文字化けする(jsdom が latin1 と解釈するため)。実サイトのHTMLは大抵宣言しているので問題にならない。

**リンクが相対パスのまま残る。** `[Obsidian](/obsidian)` のような形。Vault に保存すると壊れるので、引用時は絶対URLに直すか、リンクを外す。

**URL は必ずクォートする。** fish ではクエリや括弧がグロブとして展開される。

## Vault への保存

保存先は `Inbox/`(未仕分けの置き場)。**保存する前に、ファイル名と保存先をユーザーに確認する。**

読み終えたら、`Inbox/` から `Reference/`(恒久的な資料)へ昇格させるか、消す。`Inbox/` は溜める場所ではない。

```bash
URL="https://example.com/article"
TITLE=$(defuddle parse "$URL" -p title)
DOMAIN=$(defuddle parse "$URL" -p domain)
UID=$(date +%Y%m%d%H%M%S)
NOW=$(date '+%Y-%m-%d %H:%M:%S')
```

frontmatter は `template/uniquenote.md` のスキーマに、出典の2キーだけを足す。
クリップ記事は自分で書いたノートではないので、`source_url` と `fetched` が無いと後から検証できない。

```yaml
---
title: 記事のタイトル
aliases:
uid: "20260709111500"
created: 2026-07-09 11:15:00
updated: 2026-07-09 11:15:00
tags:
  - clip
source_url: https://example.com/article
fetched: 2026-07-09
---
```

> **タグは `clip` 単体にする。`ref/clip` にしてはいけない。**
> `Inbox/` は auto-note-mover の除外フォルダではないため、`ref/` `memo/` `blog/` `study/` で始まるタグを
> 付けた瞬間に対応フォルダへ自動移動する。仮置きにならない。`clip` はどの規則にも一致しないので `Inbox/` に留まる。
>
> 昇格させるときにタグを `ref/<話題>` に書き換えれば、auto-note-mover が `Reference/` へ運ぶ。

本文はそのまま貼る。**要約したり書き換えたりしない。** 抜き出した原文であることに価値がある。
自分の考えを足したいなら、それは別ノートにして `[[リンク]]` で結ぶ。

保存したら、`/lint` の孤立ノート指摘を避けるため、関連する MOC への1行リンクを提案する。

## 使いどころ

- ブログ執筆で外部記事を引用する前に、原文を手元に置く
- 消えそうな記事を保存する
- 長い記事を読む前に `-p description` と `-p wordCount` で当たりを付ける

`Study/` には保存しない(学習ノートは手書きの領域)。`Blog/` にも保存しない(記事執筆は `obsidian-blog` スキルの領分)。

## プライバシー

defuddle はローカルで動く。取得も解析もこのマシン内で完結し、どこにも送信しない。
