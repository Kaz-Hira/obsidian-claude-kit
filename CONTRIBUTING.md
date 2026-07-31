# 貢献するには

## 先に知っておいてほしいこと

**このリポジトリの中身は生成物です。** 単一の真実は作者の手元で稼働している
`~/dotfiles/.claude` にあり、`tools/sync-from-source.py` がホワイトリストで同期し、
個人パスを外しています。

つまり `commands/` `hooks/` `scripts/` `skills/` `agents/` への PR は、
そのままでは次回の同期で**上書きされて消えます**。実際には作者が同期元に取り込んでから
再同期する形になるので、レビューは少し遅くなります。先に Issue で相談してもらえると確実です。

上書きされないのは次のものです。ここへの PR は歓迎します。

- `README.md` / `docs/` / `CONTRIBUTING.md`
- `install.sh`
- `tools/sync-from-source.py`
- `.github/`
- `settings.example.json`

## 特に歓迎するもの

- **Linux 対応。** 現状は macOS 専用です。フックが BSD の `stat -f %m` や `date` の
  挙動に依存しているので、そこを両対応にする PR は大歓迎です
- **install.sh の堅牢化。** 想定外の環境で壊れた報告と、その修正
- **ドキュメントの誤り。** 特に `docs/vault-conventions.md` は運用の前提を書いた場所なので、
  読んで意味が通らなかった箇所の指摘が助かります

## 出す前に確認すること

CI が回しているのと同じことを手元で:

```bash
# 個人パス・鍵の混入(1件でもあれば公開事故)
grep -rniE '/Users/[A-Za-z0-9]|ghp_[A-Za-z0-9]|sk-[A-Za-z0-9]{15}' --exclude-dir=.git .

# 構文
find . -name '*.py' -not -path './.git/*' -print0 | xargs -0 -n1 python3 -m py_compile
find . -name '*.sh' -not -path './.git/*' -print0 | xargs -0 -n1 bash -n
```

**構文チェックを通ったことは、動くことを意味しません。** このコードベースは実際に
それで痛い目を見ています(PEP 604 の型注釈は `py_compile` を通って `def` の実行時に
落ちるため、RSS 収集が3日間サイレントで死んでいました)。フックやスクリプトを触ったら、
必ず一度**実際に走らせて**ください。

## Issue を出すとき

フック関係の不具合は、次の3つがあると原因が一発で分かります。

1. `~/.claude/scripts/.vault-failures.jsonl` の中身(失敗したジョブが記録されています)
2. 該当するログ(`~/.claude/scripts/vault-*.log`)
3. `python3 ~/.claude/scripts/vault-status.py` の出力

## ライセンス

PR を出した時点で、MIT ライセンスでの配布に同意したものとみなします。
