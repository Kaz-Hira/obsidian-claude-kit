#!/usr/bin/env python3
"""週次ヘルスダイジェスト。

vault-lint.py の集計・git の週次差分・Inbox 滞留・ccusage のトークン使用量を、
その週の週次ノート(daily/weekly/YYYY-Www.md)に冪等に書き込む。マーカーで囲んだ
1ブロックだけを毎回置き換えるので、再実行しても重複しない。

判断は下さない。「要トリアージ」の見出しで数字と一覧を置くだけ。中身の正否は
人間(または /lint)が読んで決める。

トリガーは「週次ノートの作成」。Templater が週次テンプレ末尾から即時に起動し、
取りこぼしは SessionStart フック(vault-weekly-lazy.sh)が次セッションで拾う。
launchd は TCC で ~/Documents に触れないため使わない。
"""

from __future__ import annotations  # 3.9 の /usr/bin/python3 で実行されても

# `X | None` 等の PEP 604 が def 実行時に評価されないようにする(2026-07-26)。
# これが無いと py_compile は通るのに実行時 TypeError で即死する。
import argparse
import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
SCRIPTS = _envpath("CLAUDE_DIR", "~/.claude") / "scripts"
LINT = SCRIPTS / "vault-lint.py"
SEARCH = _envpath("CLAUDE_DIR", "~/.claude") / "vault-search/vault_search.py"
SUGGEST = SCRIPTS / "vault-suggest-links.py"
AUTOTAG = SCRIPTS / "vault-autotag.py"
MOC_AUDIT = SCRIPTS / "vault-moc-audit.py"
LIFECYCLE = SCRIPTS / "vault-lifecycle.py"
STUDY_STATUS = SCRIPTS / "vault-study-status.py"
UV = "/opt/homebrew/bin/uv"
CCUSAGE = "/opt/homebrew/bin/ccusage"
INBOX_STALE_DAYS = 7
KANBAN_STALE_DAYS = 14   # 下書き/推敲がこの日数動かなければ「詰まり」として出す
LIFECYCLE_PICK = 2       # 要更新・淘汰候補から毎週差し出す件数(総量でなく順番待ち)
AI_LIMIT = 8            # 接続提案・タグ提案の対象上限(gpt-oss は遅いので抑える)
START = "<!-- vault-weekly:start -->"
END = "<!-- vault-weekly:end -->"


def sh(args, **kw) -> str:
    """stdout だけ返す。失敗しても空文字で続行するが、黙って消えないよう stderr に出す。

    ここが空を返すと週次ダイジェストの節が「対象なし」と見分けが付かなくなる。
    """
    p = subprocess.run(args, capture_output=True, text=True, check=False, **kw)
    if p.returncode != 0:
        print(f"  ! 失敗 rc={p.returncode}: {' '.join(map(str, args))[:100]}", file=sys.stderr)
    return p.stdout.strip()


def run(args) -> tuple[int, str]:
    """(returncode, stdout) を返す。Ollama 停止などの失敗を呼び出し側で判定するため。"""
    p = subprocess.run(args, capture_output=True, text=True, check=False)
    return p.returncode, p.stdout.strip()


def lint_summary() -> str:
    """vault-lint.py の出力から '重大 X / 警告 Y / 提案 Z' の行を拾う。"""
    out = sh(["python3", str(LINT)])
    for line in out.splitlines():
        if line.startswith("重大"):
            return line.strip()
    return "(lint 集計を取得できず)"


def git_week() -> tuple[list[str], list[str]]:
    """今週 追加/変更されたノート(.md のみ、daily と添付は除く)。"""
    since = "7 days ago"
    added = sh(["git", "-C", str(VAULT), "log", f"--since={since}",
                "--diff-filter=A", "--name-only", "--pretty=format:"])
    changed = sh(["git", "-C", str(VAULT), "log", f"--since={since}",
                  "--diff-filter=M", "--name-only", "--pretty=format:"])

    skip = ("daily/", ".trash/", "template/", "attachments/")

    def notes(raw: str) -> list[str]:
        seen = []
        for f in raw.splitlines():
            if f.endswith(".md") and not f.startswith(skip) and f not in seen:
                seen.append(f)
        return seen

    return notes(added), notes(changed)


def inbox_stale() -> list[str]:
    """Inbox にあり、作成から INBOX_STALE_DAYS 日以上たったノート。"""
    cutoff = dt.datetime.now().timestamp() - INBOX_STALE_DAYS * 86400
    out = []
    inbox = VAULT / "Inbox"
    if inbox.is_dir():
        for p in sorted(inbox.glob("*.md")):
            if p.stat().st_mtime < cutoff:
                out.append(p.name)
    return out


def ai_sections() -> list[str]:
    """接続提案(孤立→近いノート)とタグ提案(Inbox 未タグ)。Ollama 依存。

    停止していれば節ごと省き、その旨だけ1行残す。判断は書かない — 候補の列挙のみ。
    """
    # 索引を最新化。これが失敗するなら Ollama が落ちているとみなし、AI 節を諦める
    rc, _ = run([UV, "run", "--quiet", str(SEARCH), "index"])
    if rc != 0:
        return ["- **接続提案 / タグ提案**: Ollama 停止のためスキップ"]

    out = []
    # ① 孤立ノートへのリンク候補。出力は「path 行」+「→ 候補 行」の2行1組で来る。
    #    候補ありの組だけ載せる(「近い候補なし」の孤立は割愛)。
    _, sug = run([UV, "run", "--quiet", str(SUGGEST), "--limit", str(AI_LIMIT)])
    sl = sug.splitlines()
    pairs = [(sl[i], sl[i + 1]) for i in range(0, len(sl) - 1, 2)
             if sl[i + 1].lstrip().startswith("→") and "(近い候補なし)" not in sl[i + 1]]
    # 上限を設ける。全部並べるとダイジェストが一覧になって読まれなくなる。
    # ここは【入口】であって一覧ではない — 繋ぐ判断は /suggest-links で会話にする。
    SUGGEST_MAX = 5
    out.append("- **接続提案(孤立ノート→近い既存ノート)**")
    if pairs:
        for path_line, arrow_line in pairs[:SUGGEST_MAX]:
            out.append(f"    - {path_line.strip()}")
            out.append(f"        {arrow_line.strip()}")
        if len(pairs) > SUGGEST_MAX:
            out.append(f"    - (他 {len(pairs) - SUGGEST_MAX} 件)")
        out.append("    - 繋ぐなら `/suggest-links`(承認してから編集する)")
    else:
        out.append("    - 強い候補なし")

    # ④ Inbox 未タグノートへのタグ提案(dry-run。--apply しない=書き込まない)
    _, tag = run(["python3", str(AUTOTAG), "--limit", str(AI_LIMIT)])
    props = [line for line in tag.splitlines() if line.startswith("[")]
    out.append("- **タグ提案(Inbox 未タグ / 承認は手動 `vault-autotag.py --apply`)**")
    out += [f"    - {line}" for line in props] if props else ["    - 対象なし"]
    return out


def usage_summary() -> list[str]:
    """今週のトークン使用量(ccusage)。

    定額プランなので totalCost は請求額ではなく「API 単価で換算するといくらか」の
    目安。節約の動機は金銭ではなく速度と文脈汚染なので、cache read 比率を併記する
    (グローバル CLAUDE.md のサブエージェント規律が効いているかの指標になる)。
    ccusage が無い/失敗しても週次ダイジェスト全体は落とさない。
    """
    if not Path(CCUSAGE).exists():
        return ["- **トークン使用量**: (ccusage 未検出。`brew install ccusage`)"]
    # --offline: 価格表の取得で待たされない。ネットワーク断でも動く。
    rc, out = run([CCUSAGE, "weekly", "--json", "--offline"])
    if rc != 0 or not out:
        return ["- **トークン使用量**: (ccusage の取得に失敗)"]
    # ccusage は更新が速い。将来 JSON の形が変わっても週次ノートを壊さない。
    try:
        weeks = json.loads(out)["weekly"]
        by_period = {w["period"]: w for w in weeks}
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
        return ["- **トークン使用量**: (ccusage の出力を解釈できず。形式が変わった可能性)"]

    # period は ISO 週の月曜。週次ノート(YYYY-Www)と同じ週を拾う。
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    cur = by_period.get(monday.isoformat())
    if cur is None:
        return [f"- **トークン使用量**: 今週({monday} 〜)の記録なし"]

    total = cur.get("totalTokens", 0)
    cache_read = cur.get("cacheReadTokens", 0)
    cost = cur.get("totalCost", 0.0)
    ratio = (cache_read / total * 100) if total else 0.0

    lines = [
        (
            f"- **トークン使用量(今週)**: {total/1e6:.1f}M tokens / "
            f"API換算 ${cost:,.0f}(**定額プランなので請求額ではない**)"
        )
    ]

    prev = by_period.get((monday - dt.timedelta(days=7)).isoformat())
    if prev and prev.get("totalCost"):
        delta = (cost / prev["totalCost"] - 1) * 100
        lines.append(f"    - 前週比: {delta:+.0f}%(前週 API換算 ${prev['totalCost']:,.0f})")

    lines.append(
        f"    - うち cache read {cache_read/1e6:.0f}M({ratio:.0f}%)。"
        "比率が高いのは正常(同じ文脈を読み直しているだけ)。"
        "**総量が跳ねた週は、探索をサブエージェントに逃がせていない疑い**"
    )
    models = cur.get("modelsUsed") or []
    if models:
        lines.append(f"    - 使ったモデル: {', '.join(models)}")
    return lines


def moc_section() -> list[str]:
    """MOC 未収録の件数だけを出す(一覧は出さない)。

    moc-audit スキルは起動0回だった。品質の問題ではなく「起動を思い出す機会が
    無い」ことが原因なので、週次で件数だけ目に入るようにして入口にする。
    LLM 非依存・高速なので AI 節と違って必ず出せる。
    """
    try:
        spec = importlib.util.spec_from_file_location("vault_moc_audit", MOC_AUDIT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.analyze()
    except Exception as e:  # noqa: BLE001 — 週次全体を巻き込まない。ただし黙って節を消さず報告する
        print(f"  ! MOC 監査に失敗({type(e).__name__}) — この節を飛ばす", file=sys.stderr)
        return []
    n = len(r["uncovered"])
    if n == 0:
        return ["- **MOC 網羅性**: 未収録なし"]
    detail = " / ".join(f"{k} {v}" for k, v in sorted(r["by_folder"].items(),
                                                     key=lambda kv: -kv[1]))
    return [f"- **MOC 未収録**: {n} 件({detail})。一覧は `moc-audit` スキル"]


def lifecycle_section() -> list[str]:
    """ノートのライフサイクル(更新・淘汰)の順番待ちを出す。

    ここは**バックログではなく順番待ち**として出す。「古いノート 47 件」のような
    総量は 0 にならないので警報疲れになる(この Vault は既に一度それで失敗した)。
    代わりに毎週「次に手を付ける数件」だけを固定数で出す。件数が増えても
    行数は増えないので、読み飛ばされない。

    健全数も併記する——減点だけ出すダッシュボードは読まれなくなるし、
    「使われていて期限内」が増えているかがこの仕組みの本来の成績表でもある。
    """
    try:
        spec = importlib.util.spec_from_file_location("vault_lifecycle", LIFECYCLE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.analyze()
    except Exception as e:  # noqa: BLE001 — 週次全体を巻き込まない。ただし黙って節を消さず報告する
        print(f"  ! ライフサイクル解析に失敗({type(e).__name__}) — この節を飛ばす", file=sys.stderr)
        return []

    c = r["counts"]
    out = [(f"- **ライフサイクル**: 健全 {c['healthy']} / 要更新 {c['refresh']} / "
            f"淘汰候補 {c['retire']}(対象 {c['scope']} ノート)")]
    for row in r["refresh"][:LIFECYCLE_PICK]:
        out.append(f"    - 🔧 [[{row['stem']}]] — 期限+{row['overdue_days']}日 / "
                   f"被リンク{row['inbound']} → `/refresh`")
    for row in r["retire"][:LIFECYCLE_PICK]:
        out.append(f"    - 🗑️ [[{row['stem']}]] — {row['age_days']}日 無参照 → `/retire`")
    return out


def study_section() -> list[str]:
    """今週の想起(思い出した日数)を出す。

    週次には「作った量」の指標(新規ノート・更新ノート)しか無かった。
    生成だけを測ると生成だけが伸びるので、消費側の指標をここに置く。

    出すのは **7日のうち何日やったか** であって正答率ではない。点数を測ると
    点数を守る行動(簡単なカードだけ回す)が出るし、そもそも正答率は
    Anki の出題内容で動くので週ごとに比較できない。学習で唯一まともに
    比較できるのは「継続したか」なので、それだけを出す。

    総量(未着手 189 枚)は 0 にならないので出さない——ライフサイクル節と
    同じ理由で、警報疲れを招くだけになる。
    """
    if not STUDY_STATUS.is_file():
        return []
    try:
        spec = importlib.util.spec_from_file_location("vault_study_status", STUDY_STATUS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.analyze()
    except Exception as e:  # noqa: BLE001 — 週次全体を巻き込まない。ただし黙って節を消さず報告する
        print(f"  ! 想起状況の取得に失敗({type(e).__name__}) — この節を飛ばす", file=sys.stderr)
        return []
    if not r.get("available"):
        return []

    d7 = r["days_studied_7"]
    due = r["counts"]["due"]
    out = [f"- **今週の想起**: 7日中 {d7} 日実施 / 期限切れ {due} 枚"]
    if d7 == 0:
        out.append("    - 今週は一度も思い出していない → `/drill`(10問・5分)")
    elif due >= 20:
        out.append(f"    - 期限切れが {due} 枚たまっている → `/drill 15`")
    for s in r["struggling"][:LIFECYCLE_PICK]:
        out.append(f"    - ×{s['lapses']} [{s['deck']}] {s['question'][:36]} ← [[{s['source']}]]")
    return out


def kanban_stalled() -> list[str]:
    """ブログカンバンの「下書き」「推敲」で止まっているカードを出す。

    Inbox 滞留(入口)は測っていたのに、出口の滞留は測っていなかった。
    これが「生成過剰・消費不足」の非対称を見えなくしていた一因。
    公開3本に対し下書き0・ネタ0で、推敲中の1本は18日動いていない状態だった。
    """
    board = VAULT / "Blog" / "ブログ管理.md"
    if not board.is_file():
        return []
    text = board.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    cutoff = dt.datetime.now().timestamp() - KANBAN_STALE_DAYS * 86400

    # 「## ✍️ 下書き」「## 🔍 推敲」の節だけを見る(公開済みは対象外)
    for section in ("✍️ 下書き", "🔍 推敲"):
        m = re.search(rf"^## {re.escape(section)}\s*$(.*?)(?=^## |\Z)",
                      text, re.MULTILINE | re.DOTALL)
        if not m:
            continue
        for link in re.findall(r"\[\[([^\]|#]+)", m.group(1)):
            p = next(VAULT.rglob(f"{link.strip()}.md"), None)
            if p is None or p.stat().st_mtime >= cutoff:
                continue
            days = int((dt.datetime.now().timestamp() - p.stat().st_mtime) // 86400)
            out.append(f"    - {section}: [[{link.strip()}]] が {days} 日停止")

    # ネタ列が空なら、それ自体が詰まりのサイン(入口が無ければ出口も動かない)
    idea = re.search(r"^## 💡 ネタ\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    empty_idea = idea is not None and not re.search(r"^\s*-", idea.group(1), re.MULTILINE)

    if not out and not empty_idea:
        return []
    head = ["- **ブログの詰まり**"]
    if empty_idea:
        head.append("    - ネタ列が空。`/triage` でブログネタの処遇を使うと溜まる")
    return head + out


def build_block(with_ai: bool = True) -> str:
    now = dt.datetime.now()
    crit = lint_summary()
    added, changed = git_week()
    stale = inbox_stale()

    lines = [
        START,
        "## 🩺 週次ヘルスダイジェスト",
        "",
        f"> 自動生成 {now:%Y-%m-%d %H:%M} / **要トリアージ**。数字は機械検出で、正否は `/lint` で確認する。",
        "",
        f"- **健全性**: {crit}(詳細は `/lint`)",
    ]
    lines += usage_summary()
    lines.append(f"- **今週の新規ノート**: {len(added)} 件")
    lines += [f"    - [[{Path(f).stem}]]" for f in added[:10]]
    lines.append(f"- **今週 更新したノート**: {len(changed)} 件")
    lines += [f"    - [[{Path(f).stem}]]" for f in changed[:10]]
    lines.append(f"- **Inbox 滞留(≥{INBOX_STALE_DAYS}日)**: {len(stale)} 件")
    lines += [f"    - `Inbox/{f}`" for f in stale[:10]]
    lines += moc_section()
    lines += lifecycle_section()
    lines += study_section()
    lines += kanban_stalled()
    # Anki の催促はここに置かない(2026-07-26 に削除)。
    # .apkg は生涯1本しか作られておらず、本人も「不要」と判断している。
    # 望んでいないものを毎週促すのは警報疲れの一種で、ダイジェスト全体の
    # 信用を下げる。/anki と vault-anki.py 自体は残してあるので、要るときに叩けばよい。
    if with_ai:
        lines += ai_sections()
    lines += ["", END]
    return "\n".join(lines)


def locale_week(d: dt.date) -> tuple[int, int]:
    """Templater / Periodic Notes と同じ週番号(moment の locale week)を返す。

    この Vault の週次ノートは moment の `YYYY-[W]ww`(en ロケール)で命名されている:
    週の始まりは【日曜】で、week 1 は 1/1 を含む週。実データで確認済み——
    `2026-W29.md` は 07-12(日)〜07-18(土) を埋め込んでいる。

    以前ここは `isocalendar()`(ISO 週= 月曜始まり)を使っていた。両者は
    月〜土は一致するが【日曜だけ 1 ずれる】(ISO は日曜を前の週の最終日、
    locale は次の週の初日として扱う)。そのため日曜に走ると隣の週のノートに
    ダイジェストを書き込む取り違えが起きていた。2026-07-26(日)に発覚。
    """
    jan1 = dt.date(d.year, 1, 1)
    # その日を含む週の開始日(直前の日曜)
    start_of_d = d - dt.timedelta(days=(d.weekday() + 1) % 7)
    start_of_week1 = jan1 - dt.timedelta(days=(jan1.weekday() + 1) % 7)
    if start_of_d < start_of_week1:  # 年初の数日は前年の最終週に属する
        jan1 = dt.date(d.year - 1, 1, 1)
        start_of_week1 = jan1 - dt.timedelta(days=(jan1.weekday() + 1) % 7)
        return d.year - 1, (start_of_d - start_of_week1).days // 7 + 1
    return d.year, (start_of_d - start_of_week1).days // 7 + 1


def week_days(year: int, week: int) -> list[dt.date]:
    """その週の 日曜〜土曜 の7日を返す(テンプレの tp.date.weekday 0..6 と同じ)。"""
    jan1 = dt.date(year, 1, 1)
    start_of_week1 = jan1 - dt.timedelta(days=(jan1.weekday() + 1) % 7)
    start = start_of_week1 + dt.timedelta(weeks=week - 1)
    return [start + dt.timedelta(days=i) for i in range(7)]


def render_weekly_template(year: int, week: int) -> str:
    """`template/templater/weeklynote_temp.md` の【解決済みの姿】を返す。

    週次ノートを機械が新規作成するとき、ダイジェストだけを書くと Templater 本体
    (レビューチェックリスト・前後週リンク・7日分の Memo/Diary 埋め込み)が入らない。
    しかもファイルが存在してしまうので、後から Periodic Notes で開いてもテンプレは
    適用されない——週次レビューを自動化するつもりで、レビューの器を壊すことになる。
    そこで `/daily` と同じ「テンプレの解決済みの姿を CLI 側で再現する」方式を採る。

    テンプレを変更したら【ここも直す】。ズレると週次レビューの体裁が崩れる。
    """
    days = week_days(year, week)
    title = f"{year}-W{week:02d}"
    prev_y, prev_w = locale_week(days[0] - dt.timedelta(days=7))
    next_y, next_w = locale_week(days[0] + dt.timedelta(days=7))
    # 「今年の残り週数」= 年末までの週数(テンプレの moment(...).diff(title, "w") 相当)
    weeks_left = (dt.date(year, 12, 31) - days[0]).days // 7
    wd_ja = "日月火水木金土"

    out = [
        "---", "tags:", '  - "weekly"', f'  - "{title}"', "---", "",
        f"#### << [[{prev_y}-W{prev_w:02d}|Last week]] | [[{next_y}-W{next_w:02d}|Next week]] >>", "",
        f"今年の残り週数：{weeks_left}週", "",
        "## 週次レビューチェックリスト", "",
        "- [ ] トリガーリストを見ながら気になっていることを殴り書き",
        "- [ ] 日記を読みながら今週の発見を箇条書きする",
        "- [ ] 来週の目標を箇条書きする",
        "- [ ] 来週の目標をタスク化してcalendarに入れる",
        "- [ ] 来週のスケジュール", "",
        "## 気になっていること・悩み", "", "-", "",
        "## 今週の発見", "", "-", "",
        "## 来週の目標", "", "-", "",
        "## 一週間のメモ", "",
    ]
    out += [f"![[{d:%Y-%m-%d}#Memo]]" for d in days]
    out += ["", "## 一週間のタスク", ""]
    out += [f"![[{d:%Y-%m-%d}#Diary：{d:%Y-%m-%d} ({wd_ja[(d.weekday() + 1) % 7]})]]" for d in days]
    return "\n".join(out)


def weekly_note_path(now: dt.datetime) -> Path:
    y, w = locale_week(now.date())
    return VAULT / "daily" / "weekly" / f"{y}-W{w:02d}.md"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true",
                    help="接続提案・タグ提案(Ollama 依存で遅い)を省く")
    ap.add_argument("--print-week", action="store_true",
                    help="今週の週次ノート名(YYYY-Www)だけを出して終了。"
                         "フックが同じ週計算を使うための唯一の入口。")
    args = ap.parse_args()

    now = dt.datetime.now()
    if args.print_week:
        y, w = locale_week(now.date())
        print(f"{y}-W{w:02d}")
        return
    block = build_block(with_ai=not args.no_ai)
    note = weekly_note_path(now)
    note.parent.mkdir(parents=True, exist_ok=True)

    if note.exists():
        text = note.read_text(encoding="utf-8")
        if START in text and END in text:
            # 置換文字列は【ラムダで渡す】。文字列を直接渡すと block 内のバックスラッシュが
            # 後方参照として解釈される。ノート名に LaTeX が入っていると
            # `[[\alpha 崩壊]]` の `\a` が BEL(\x07)に化けて週次ノートに書かれる——
            # 例外にならず【黙って壊れる】ので気づけない(2026-07-27 に検出)。
            text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block,
                          text, flags=re.DOTALL)
        else:
            text = text.rstrip() + "\n\n" + block + "\n"
    else:
        # 新規作成時はテンプレ本体も一緒に書く。ダイジェストだけ書くと
        # 週次レビューの器(チェックリスト・日次埋め込み)が永久に入らなくなる。
        y, w = locale_week(now.date())
        text = render_weekly_template(y, w).rstrip() + "\n\n" + block + "\n"

    note.write_text(text, encoding="utf-8")
    print(f"週次ダイジェストを書き込みました: {note.relative_to(VAULT)}")


if __name__ == "__main__":
    main()
