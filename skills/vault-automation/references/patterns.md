# Vault 自動化の部品と型

SKILL.md から参照される実装リファレンス。**書き始める前に該当箇所を読む。**
ここに載っているものは全部この Vault で実際に動いているコードで、
パスはそのまま使える(`~/.claude/` は `~/dotfiles/.claude/` への symlink)。

## 目次

1. [共通ライブラリ](#共通ライブラリ)
2. [フックの骨格](#フックの骨格)
3. [バックグラウンドジョブを足す](#バックグラウンドジョブを足す)
4. [通知を足す](#通知を足す)
5. [ノートに冪等に書き込む](#ノートに冪等に書き込む)
6. [python スクリプトの型](#python-スクリプトの型)
7. [設定ファイルの触り方](#設定ファイルの触り方)

---

## 共通ライブラリ

`~/.claude/hooks/lib/vault-common.sh` — source して使う。

| 関数 | 返り値 | 用途 |
|---|---|---|
| `vault_scope_check <cwd>` | 0=Vault 配下 | フック冒頭。`\|\| exit 0` で使う |
| `vault_python` | stdout に絶対パス / 非0 | python3 >= 3.10 を**実際に聞いて**解決 |
| `vault_bg_run <job> <log> <cmd…>` | 0=起動 1=ロック中 2=ブレーカ 3=python無し | 成功時のみスタンプを進める detach 実行 |

`~/.claude/hooks/lib/vault-nudge.sh` — 通知の抑制。

| 関数 | 用途 |
|---|---|
| `nudge_should_emit <key> <value> <threshold> [min_days]` | 0=出してよい |
| `nudge_record <key> <value>` | 出した事実を記録 |
| `nudge_reset <key>` | 解消したら消す(次に再発したとき初回扱いで鳴る) |

状態は `~/.claude/scripts/.vault-nudge.json`。`NUDGE_STATE` 環境変数で差し替えられるので、
テスト時はスクラッチに逃がす。

---

## フックの骨格

全フック共通。**Vault 外では何もしない**のが最重要(グローバル登録なので、
`~/dev` での作業に干渉すると事故になる)。

```bash
#!/usr/bin/env bash
# <イベント> フック: 何をするか。なぜこの設計かを2〜4行で。
# 過去の失敗を踏まえた判断があるなら日付つきで書く(後から読む人のため)。

set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

VAULT="{{VAULT}}"
export VAULT

# shellcheck source=lib/vault-common.sh
. {{CLAUDE_DIR}}/hooks/lib/vault-common.sh

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
vault_scope_check "$cwd" || exit 0

# …ここから本体…
exit 0
```

### イベント別の入出力

**SessionStart** — 文脈を足すなら:
```bash
jq -nc --arg c "$body" \
  '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$c}}'
```
何も言うことがなければ**無出力で exit 0**。`vault-hot-cache.sh` の
`additionalContext` を汚さないよう、stdout に素のテキストを垂れ流さない。

**PreToolUse** — 止める/確認する:
```bash
path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
jq -nc --arg d "ask" --arg r "理由" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$d,permissionDecisionReason:$r}}'
```
`permissionDecision` は `allow` / `deny` / `ask`。素通りさせるときは**何も出さずに exit 0**。

**Stop** — 終了前に言わせる:
```bash
active=$(printf '%s' "$input" | jq -r '.stop_hook_active // false')
[ "$active" = "true" ] && exit 0   # 無限ループ防止。必ず入れる

jq -nc --arg r "$reasons" '{decision:"block", reason:$r}'
```

**PostToolUse** — 非ブロッキングで知らせる。**常に exit 0**(編集を止めない)。

### 日本語ファイル名

`git` 系を呼ぶときは `-c core.quotepath=false` を付ける。無いと
`\343\203\236` のような8進エスケープで出て通知が読めない。この Vault は日本語ノートが大半。

```bash
git -c core.quotepath=false status --porcelain
git -c core.quotepath=false ls-files --others --exclude-standard -- '*.md'
```

---

## バックグラウンドジョブを足す

### 手順

1. `~/.claude/scripts/vault-<name>.py` を書く(下の [python スクリプトの型](#python-スクリプトの型))
2. `~/.claude/scripts/vault-bg-run.sh` の `case` にジョブ名を1行足す
3. 起動元(フック / Templater)は `vault-bg-run.sh <job>` だけを呼ぶ
4. `.gitignore` に `-lock` / `-stamp` を足す(既にワイルドカードで入っているはず)

### vault-bg-run.sh への追加

```bash
case "$job" in
  rss)      script="$SCRIPTS/vault-rss.py" ;;
  weekly)   script="$SCRIPTS/vault-weekly.py" ;;
  hot-auto) script="$SCRIPTS/vault-hot-auto.py" ;;
  <new>)    script="$SCRIPTS/vault-<new>.py" ;;   # ← 足す
  *) echo "unknown job: $job" >&2; exit 64 ;;
esac
```

### 起動側(フック)

```bash
VAULT_GUARD=$((15 * 60))   # 二重起動防止の窓
export VAULT_GUARD

# トリガー条件と冪等性チェックを先に書く。
# 「成果物が既にあるなら何もしない」が冪等性を担う。
[ -f "$TRIGGER" ] || exit 0
[ -f "$OUTPUT" ] && exit 0

{{CLAUDE_DIR}}/scripts/vault-bg-run.sh <job> >/dev/null 2>&1
exit 0
```

### Templater から呼ぶ場合

`.obsidian/plugins/templater-obsidian/data.json` の `templates_pairs` に入れる。
**JSON を手で編集せず python で書き換える**(エスケープを壊しやすい)。

```python
import json, pathlib
p = pathlib.Path(".obsidian/plugins/templater-obsidian/data.json")
d = json.loads(p.read_text(encoding="utf-8"))
for pair in d.get("templates_pairs", []):
    if pair[0] == "<name>":
        pair[1] = "nohup {{CLAUDE_DIR}}/scripts/vault-bg-run.sh --delay 3 <job> >/dev/null 2>&1 &"
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

`--delay N` は「Obsidian がノートを書き終えるのを待つ」ため。Templater は
**ノートが作られる瞬間**に走るので、待たずに読むと空のノートを相手にする。
SessionStart フックからの起動は「ノートが既にある」ことが条件なので不要。

---

## 通知を足す

### 型

```bash
. {{CLAUDE_DIR}}/hooks/lib/vault-nudge.sh

if [ "$value" -ge "$THRESHOLD" ]; then
  if nudge_should_emit "<key>" "$value" "$THRESHOLD" 1; then
    add "📥 <数字> → \`<次の1アクション>\`"
    nudge_record "<key>" "$value"
  fi
else
  nudge_reset "<key>"       # 解消したら消す。次の再発で初回扱いになる
fi
```

### 文面の規則

- 「**数字 + 次の1アクション**」だけ。解説を書かない(解説はノートの仕事)
- 総量に上限をつけ、超過は `(他 n 件)` に畳む
- 閾値を1つも超えなければ**何も出さない**

### 閾値の決め方

現在値を入れて出力行数を検算する。今日3行出るなら、明日も3行出る。
「平常時に何行出るか」が設計値で、それが2行を超えるなら閾値が緩すぎる。

---

## ノートに冪等に書き込む

マーカーで囲んだブロックだけを置換する。`vault-weekly.py` / `vault-hot-auto.py` が前例。

```python
START = "<!-- <name>:start -->"
END = "<!-- <name>:end -->"

if START in text and END in text:
    text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block,
                  text, flags=re.DOTALL)
else:
    text = text.rstrip() + "\n\n" + block + "\n"
```

`re.sub` の置換に**ラムダを使う**。文字列を直接渡すと、block 内の `\1` や `\g` が
後方参照として解釈されて壊れる。

### 機械と人でブロックを分ける

同じノートに機械生成と人/AI の判断を書くなら、**別のマーカー**にする。

| ブロック | 誰が書くか |
|---|---|
| `vault-weekly:start/end` | python が毎回上書き |
| `vault-weekly:ai:start/end` | サブエージェントの結論 |
| `hot:auto:start/end` | git から機械生成 |

混ぜると次回の実行で人の書いたものが消える。

### frontmatter の鮮度キーを分ける

`updated:` は**人が書いた部分**の鮮度。機械更新で触ると「古い」判定が機能しなくなるので、
機械側は `auto-updated:` を別に持つ。

### 変化がなければ書かない

```python
if new == text:
    return 0   # git の差分ノイズを増やさない
```

---

## python スクリプトの型

```python
#!/usr/bin/env python3
"""1行で何をするか。

なぜこの設計かを数行。過去の失敗があれば日付つきで。

  vault-<name>.py            # 通常
  vault-<name>.py --dry-run  # 書かずに出す
"""

from __future__ import annotations  # 3.9 で起動されても PEP 604 で落ちないように

import argparse
import sys
from pathlib import Path

VAULT = Path("{{VAULT}}")
SCRIPTS = Path("{{CLAUDE_DIR}}/scripts")


def analyze() -> dict:
    """データを返す。表示しない。他スクリプトから再利用できるように分ける。"""
    ...


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    r = analyze()
    ...
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 他スクリプトの analyze() を呼ぶ

ファイル名にハイフンが入っているので通常の import ができない。

```python
import importlib.util
spec = importlib.util.spec_from_file_location("vault_lint", SCRIPTS / "vault-lint.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
r = mod.analyze()
```

シェルアウトして出力を再パースしない。**判定の定義が2箇所に散って必ずズレる。**

### キャッシュ(SessionStart で使うもの)

stale-while-revalidate。`vault-status.py` が前例。

1. キャッシュが新しい → そのまま使う
2. 古い → **古いまま使い**、裏で再生成を kick(セッション開始を待たせない)
3. 無い → 初回だけ `timeout 5` 付きで同期生成

---

## 設定ファイルの触り方

### settings.json(フック登録・allowlist)

**python で書き換える。** 手で編集すると JSON を壊しやすく、壊れると全フックが黙って死ぬ。

```python
import json, pathlib, collections
p = pathlib.Path("{{DOTFILES}}/.claude/settings.json")
d = json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=collections.OrderedDict)

# SessionStart に足す
ss = d["hooks"]["SessionStart"][0]["hooks"]
ss.append(collections.OrderedDict(
    [("type","command"),("command","{{CLAUDE_DIR}}/hooks/vault-<name>.sh"),("timeout",10)]))

# allowlist に足す
d.setdefault("permissions", {}).setdefault("allow", []).append(
    "Bash(python3 {{CLAUDE_DIR}}/scripts/vault-<name>.py:*)")

p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

書いたら必ず妥当性を確認する:
```bash
python3 -c "import json;json.load(open('{{DOTFILES}}/.claude/settings.json'));print('OK')"
```

### 権限の3層

| 層 | 場所 | 入れるもの |
|---|---|---|
| user | `~/.claude/settings.json` | 全プロジェクト共通 |
| project | Vault の `.claude/settings.json` | Vault 固有の ambient 用途 |
| コマンド | 各コマンドの `allowed-tools` | **そのコマンド実行中だけ**有効 |

コマンドが呼ぶスクリプトはコマンドの frontmatter が許可するので、project 層に
足すのは冗長。project 層は「コマンド外で Claude が単独に叩くもの」だけ。

---

## テストの型

```bash
# フック: Vault 内 / Vault 外 / 抑制の3つを必ず見る
H=~/.claude/hooks/vault-<name>.sh
run(){ echo "{\"cwd\":\"$1\"}" | NUDGE_STATE=/tmp/n.json $H; }

rm -f /tmp/n.json
run {{VAULT}}   # 出るはず
run {{VAULT}}   # 同日2回目 → 黙るはず
run {{DOTFILES}}                   # Vault 外 → 無出力のはず
```

`vault_bg_run` を使うものは、成功と失敗の両方を試す。
`VAULT_STATE_DIR` を差し替えれば本番の状態ファイルを汚さずに試せる。

```bash
export VAULT_STATE_DIR=/tmp/bgtest && mkdir -p "$VAULT_STATE_DIR"
. ~/.claude/hooks/lib/vault-common.sh
vault_bg_run demo /tmp/t.log /bin/sh -c 'exit 7'   # → stamp が進まないこと
```
