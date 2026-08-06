#!/usr/bin/env python3
"""RSS/Atom を取得 → ローカル要約 → Inbox に1本のダイジェストとして落とす。

未接続だった gpt-oss:20b の定常業務。API課金ゼロの情報収集レイヤー。
記事ごとにノートを乱造せず、その日の新着を1本の `Inbox/YYYYMMDD-RSS.md` にまとめる。
深追いしたい記事は defuddle スキルで全文クリップに繋ぐ。

フィードは `~/.claude/scripts/vault-feeds.txt`。`[セクション名:件数]` で章分けし、
ダイジェストも同じ章立てで出る(AI / 技術 / ニュース / バズ など)。
既読は `~/.claude/scripts/.vault-rss-seen.json` で管理し、二度要約しない。

  vault-rss.py            # 取得・要約して Inbox に書く
  vault-rss.py --dry-run  # Inbox に書かず、拾った新着をセクション別に表示
  vault-rss.py --feeds    # 登録フィードの一覧と疎通確認だけ
"""

from __future__ import annotations  # 3.9 の /usr/bin/python3 で実行されても

# `X | None` 等の PEP 604 が def 実行時に評価されないようにする(2026-07-26)。
# これが無いと py_compile は通るのに実行時 TypeError で即死する。
import argparse
import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


SCRIPTS = _envpath("CLAUDE_DIR", "~/.claude") / "scripts"
VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
FEEDS = SCRIPTS / "vault-feeds.txt"
SEEN = SCRIPTS / ".vault-rss-seen.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2"     # 要約は軽いタスク。gpt-oss:20b(1件66秒)は遅すぎるので高速な小型モデルを使う
MAX_PER_FEED = 6       # 1フィードあたり見る最新件数
DEFAULT_SECTION_LIMIT = 4   # `[名前]` に件数指定が無いときのセクション上限
MAX_TOTAL = 22         # 1回で扱う総件数の上限。各セクションの上限の合計を下回ると、
                       # 後ろのセクションが枠切れで痩せる(collect の room 計算)。
                       # 現行の合計は 5+5+4+3+5=22。フィードを足したらここも上げる
SUMMARY_BUDGET = 300   # 要約に使う秒数の上限。超えた分は原文抜粋にフォールバックする
SEEN_TTL_DAYS = 60     # 既読の保持期間。これを過ぎたリンクは忘れる(state の肥大化防止)
UA = {"User-Agent": "vault-rss/1.0"}
FETCH_DELAY = 0.7      # フィード間の待ち。Reddit の 429(レート制限)を避ける

SECTION_RE = re.compile(r"^\[(.+?)(?::(\d+))?\]$")


# --- フィード定義の読み込み ------------------------------------------------

def load_feeds() -> tuple[list[tuple[str, str]], dict[str, int]]:
    """`[セクション]` 見出し付きのフィード定義を読む。

    戻り値は (セクション名, URL) の並び(定義順)と、セクション別の件数上限。
    """
    if not FEEDS.exists():
        return [], {}
    section = "その他"
    feeds: list[tuple[str, str]] = []
    limits: dict[str, int] = {}
    for line in FEEDS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()  # 行頭・行内コメントを落とす
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            limits[section] = int(m.group(2)) if m.group(2) else DEFAULT_SECTION_LIMIT
            continue
        feeds.append((section, line))
        limits.setdefault(section, DEFAULT_SECTION_LIMIT)
    return feeds, limits


# --- 取得と解析 ------------------------------------------------------------

def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


RETRY_BACKOFF = (10, 30)  # 429 のときの待ち。Reddit の .rss は IP 単位で連続アクセスに厳しい


def fetch(url: str) -> bytes | None:
    """429 だけ段階的にリトライする。他のエラーは即あきらめて次のフィードへ。"""
    for wait in (*RETRY_BACKOFF, None):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and wait is not None:
                time.sleep(wait)
                continue
            print(f"  ! 取得失敗 {url}: {e}")
            return None
        except Exception as e:  # noqa: BLE001 — ネットワーク層。1フィードの失敗で全体を止めない。必ず1行出力する
            print(f"  ! 取得失敗 {url}: {e}")
            return None
    return None


def extract_score(item: dict) -> tuple[int, str]:
    """反応数を拾う。はてブは `hatena:bookmarkcount`、HN は description の `Points:`。"""
    if item.get("bookmarkcount"):
        try:
            return int(item["bookmarkcount"]), "🔖"
        except ValueError:
            pass
    m = re.search(r"Points:\s*(\d+)", item.get("raw_summary", ""))
    if m:
        return int(m.group(1)), "▲"
    return 0, ""


def parse_feed(url: str) -> list[dict]:
    """RSS(1.0/2.0)と Atom を stdlib で読む。title/link/summary/score を返す。"""
    raw = fetch(url)
    if raw is None:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  ! 解析失敗 {url}: {e}")
        return []

    def tag(el):
        return el.tag.split("}")[-1]

    items = []
    # RSS: channel/item  /  Atom: feed/entry
    entries = [e for e in root.iter() if tag(e) in ("item", "entry")]
    for e in entries[:MAX_PER_FEED]:
        it = {"title": "", "link": "", "raw_summary": "", "bookmarkcount": ""}
        for c in e:
            t = tag(c)
            if t == "title" and not it["title"]:
                it["title"] = strip_html(c.text or "")
            elif t == "link" and not it["link"]:
                # Atom は href 属性。rel="replies" 等の副リンクは拾わない
                if c.get("rel") in (None, "alternate"):
                    it["link"] = c.get("href") or (c.text or "")
            elif t in ("description", "summary", "content") and not it["raw_summary"]:
                it["raw_summary"] = strip_html(c.text or "")
            elif t == "bookmarkcount":
                it["bookmarkcount"] = (c.text or "").strip()
        if not (it["title"] and it["link"]):
            continue
        score, badge = extract_score(it)
        items.append({
            "title": it["title"],
            "link": it["link"].strip(),
            "summary": it["raw_summary"][:600],
            "score": score,
            "badge": badge,
            "feed": url,
        })
    return items


# --- 要約 ------------------------------------------------------------------

def excerpt(summary: str, limit: int = 110) -> str:
    """要約が取れなかったときのフォールバック。フィードの概要をそのまま短く出す。"""
    s = summary.strip()
    if not s:
        return "(概要なし)"
    return (s[:limit] + "…" if len(s) > limit else s) + " ※原文抜粋"


def summarize(title: str, summary: str) -> str:
    prompt = (
        "次の記事を日本語で1〜2文に要約してください。誇張せず事実だけ。"
        "前置き・記号なしで要約文のみ出力。\n\n"
        f"タイトル: {title}\n概要: {summary}\n\n要約:"
    )
    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                             "options": {"temperature": 0.2}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as res:
            out = json.loads(res.read()).get("response", "").strip()
    except Exception as e:  # noqa: BLE001 — 要約は落ちても本文で代替する。失敗はノート本文に残るので観測できる
        return f"(要約失敗: {e}) {excerpt(summary)}"
    # 空返しは ollama の不調で普通に起きる。無言の空行を残さずフィードの概要で埋める
    return out.split("\n")[0].strip() or excerpt(summary)


# --- 既読管理 --------------------------------------------------------------

def load_seen() -> dict[str, str]:
    """{link: 最終確認日} で持つ。旧形式(リストのみ)は今日の日付を付けて移行する。"""
    if not SEEN.exists():
        return {}
    try:
        data = json.loads(SEEN.read_text())
    except (OSError, ValueError):
        # 壊れた/読めないキャッシュは作り直す。想定外の例外は伝播させる
        return {}
    today = dt.date.today().isoformat()
    if isinstance(data, list):
        return dict.fromkeys(data, today)
    return data


def prune_seen(seen: dict[str, str]) -> dict[str, str]:
    cutoff = (dt.date.today() - dt.timedelta(days=SEEN_TTL_DAYS)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


# --- 収集 ------------------------------------------------------------------

def interleave(groups: list[list[dict]], limit: int) -> list[dict]:
    """フィードごとの新着を交互に取る。1本の多産フィードが枠を食い潰さないように。"""
    out, i = [], 0
    while len(out) < limit and any(len(g) > i for g in groups):
        for g in groups:
            if len(g) > i:
                out.append(g[i])
                if len(out) >= limit:
                    break
        i += 1
    return out


def collect(feeds, limits, seen) -> list[tuple[str, list[dict]]]:
    """セクション順に (セクション名, 記事リスト) を返す。"""
    by_section: dict[str, list[list[dict]]] = {}
    order: list[str] = []
    picked: set[str] = set()   # フィードを跨いだ重複(はてブ総合×IT 等)を落とす

    for section, url in feeds:
        if section not in by_section:
            by_section[section] = []
            order.append(section)
        items = []
        for it in parse_feed(url):
            if it["link"] in seen or it["link"] in picked:
                continue
            picked.add(it["link"])
            items.append(it)
        if items:
            by_section[section].append(items)
        time.sleep(FETCH_DELAY)

    result, total = [], 0
    for section in order:
        groups = by_section[section]
        if not groups:
            continue
        room = min(limits.get(section, DEFAULT_SECTION_LIMIT), MAX_TOTAL - total)
        if room <= 0:
            break
        items = interleave(groups, room)
        # 反応数が付くセクション(バズ枠)は数字順に。付かないものは元の順を保つ
        if any(it["score"] for it in items):
            items.sort(key=lambda x: x["score"], reverse=True)
        result.append((section, items))
        total += len(items)
    return result


# --- 出力 ------------------------------------------------------------------

def render(sections, now) -> str:
    uid = now.strftime("%Y%m%d%H%M%S")
    stamp = f"{now:%Y-%m-%d %H:%M:%S}"
    parts = [
        (
            f"---\ntitle: RSSダイジェスト {now:%Y-%m-%d}\naliases:\nuid: \"{uid}\"\n"
            f"created: \"{stamp}\"\nupdated: \"{stamp}\"\n"
            # タグは空。Inbox に留めて手で取捨選択する(ref/* を付けると Auto Note Mover が Reference へ移す)
            f"tags:\n---\n# 📰 RSSダイジェスト {now:%Y-%m-%d}\n\n"
            "ローカル要約。深追いは defuddle で全文クリップ。"
        )
    ]
    for section, items in sections:
        parts.append(f"\n## {section}\n")
        for it in items:
            badge = f" `{it['badge']}{it['score']}`" if it["score"] else ""
            parts.append(f"- **[{it['title']}]({it['link']})**{badge}\n    {it['text']}")
    return "\n".join(parts) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Inbox に書かず新着だけ表示")
    ap.add_argument("--feeds", action="store_true", help="登録フィードの疎通確認だけ")
    args = ap.parse_args()

    feeds, limits = load_feeds()
    if not feeds:
        print(f"フィード未設定。{FEEDS} に 1行1URL で登録してください。")
        return

    if args.feeds:
        for section, url in feeds:
            n = len(parse_feed(url))
            print(f"  {'OK ' if n else 'NG '} [{section}] {n:2d}件  {url}")
            time.sleep(FETCH_DELAY)
        return

    seen = load_seen()
    sections = collect(feeds, limits, seen)
    total = sum(len(items) for _, items in sections)
    if not total:
        print("新着なし。")
        return

    print(f"新着 {total} 件" + ("(dry-run)" if args.dry_run else "を要約中…"))
    if args.dry_run:
        for section, items in sections:
            print(f"\n[{section}]")
            for it in items:
                badge = f" ({it['badge']}{it['score']})" if it["score"] else ""
                print(f"  - {it['title']}{badge}")
        return

    # 要約は1件5〜10秒。全体が伸びすぎないよう、予算を超えたら原文抜粋に切り替える
    deadline = time.monotonic() + SUMMARY_BUDGET
    today = dt.date.today().isoformat()
    for _, items in sections:
        for it in items:
            if time.monotonic() < deadline:
                it["text"] = summarize(it["title"], it["summary"])
            else:
                it["text"] = excerpt(it["summary"])
            seen[it["link"]] = today

    now = dt.datetime.now()
    note = VAULT / "Inbox" / f"{now:%Y%m%d}-RSS.md"
    note.write_text(render(sections, now), encoding="utf-8")
    SEEN.write_text(json.dumps(prune_seen(seen), ensure_ascii=False, sort_keys=True),
                    encoding="utf-8")
    print(f"書き込み: Inbox/{note.name}({total} 件 / {len(sections)} セクション)")


if __name__ == "__main__":
    main()
