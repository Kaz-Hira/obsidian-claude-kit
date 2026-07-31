# 変更履歴

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に従い、
バージョンは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

## [0.1.0] - 2026-08-01

最初の公開。稼働中の Obsidian Vault 運用環境から切り出したもの。

### 追加

- **スラッシュコマンド 11** — `/save` `/triage` `/lint` `/weekly-review` `/vsearch`
  `/suggest-links` `/research` `/hot` `/daily` `/anki` `/dispatch`
- **フック 9** — SessionStart でのホットキャッシュ注入・ダッシュボード通知・RSS 収集・
  週次ノート生成、PostToolUse での lint とリンク提案、PreToolUse でのフォルダガードと
  破壊的 git 操作の阻止、Stop でのキャッシュ更新促し
- **スキル 8** — `defuddle` `obsidian-blog` `voice` `moc-audit` `vault-automation`
  `obsidian-bases` `obsidian-cli` `diagnosing-bugs`
- **サブエージェント 5** — `note-synthesizer` `vault-structure-reviewer` `fact-checker`
  `code-reviewer` `security-reviewer`
- **スクリプト 11** — lint / MOC 監査 / セマンティック検索インデックス / RSS 要約 /
  週次ダイジェスト / Anki 生成 / 自動タグ付け ほか
- `install.sh` — 既存ファイルを退避してから配置し、`settings.json` は既存キーを
  残したままマージする。`--dry-run` で事前確認できる
- `tools/sync-from-source.py` — 稼働環境からの同期。個人パスの置換漏れが1件でもあれば
  異常終了する
- `docs/vault-conventions.md` — Vault 側に置く `CLAUDE.md` の雛形と、その設計理由

### 設計上の判断

- **成功したときだけスタンプを進める。** 以前は `touch $STAMP` を実行前に行っていたため、
  ジョブが失敗してもガード窓が閉じて再試行されず、RSS 収集が3日間サイレントで
  落ちていた。`vault_bg_run` はロック・成功時刻・失敗記録を3ファイルに分け、
  連続失敗はサーキットブレーカで止めてダッシュボードに出す
- **Vault の外では黙る。** 全フックが `vault_scope_check` を通り、cwd が Vault 配下で
  なければ即 exit 0 する(symlink 経由でも実パスで判定)
- **通知は最大3行。** 無条件に出すと警報疲れを起こし、本当に見るべき1行が埋もれる

### 修正

- Vault の走査対象から `.claude` を除外。Claude Code の worktree
  (`.claude/worktrees/`)には Vault の複製が丸ごと入るため、lint は同じノートを
  二重に指摘し、埋め込み索引は同じ本文を二重にベクトル化していた
  (`vault-lint` / `vault-moc-audit` / `vault-suggest-links` / `vault-autotag` /
  `vault_search` の5つに同じ欠陥があった)

[Unreleased]: https://github.com/Kaz-Hira/obsidian-claude-kit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Kaz-Hira/obsidian-claude-kit/releases/tag/v0.1.0
