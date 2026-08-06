#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = ["genanki", "markdown"]
# ///
"""Study/ のノートから Anki デッキ(.apkg)を生成する。

見出し(#〜####)ごとに1カード。表=見出し、裏=次の見出しまでの本文。空同然の
カード(見出し直下がすぐ小見出し等)は捨てる。見出しの無いノートは丸ごと1カード。
数式($…$ / $$…$$)は Anki 内蔵 MathJax の \\(…\\) / \\[…\\] に変換する。

  vault-anki.py <note.md> [note2.md ...]     # 指定ノートから
  vault-anki.py --tag study/解析学            # そのタグの全ノートから
  vault-anki.py --all                         # Study/ 全ノートから
  vault-anki.py ... -o ~/Downloads/out.apkg   # 出力先(既定 ~/Downloads)

デッキは科目タグごとに `Study::<科目>`。カードの guid は uid+見出しで安定させるので、
再生成して再インポートすると重複せず更新される。生成後は .apkg をダブルクリックで取り込む。
"""

from __future__ import annotations  # 3.9 の /usr/bin/python3 で実行されても

# `X | None` 等の PEP 604 が def 実行時に評価されないようにする(2026-07-26)。
# これが無いと py_compile は通るのに実行時 TypeError で即死する。
import argparse
import datetime as dt
import hashlib
import html as htmllib
import json
import re
import sys
import urllib.request
from pathlib import Path

import genanki
import markdown as md
import os


def _envpath(var: str, default: str) -> Path:
    """環境変数 var があればそれを、無ければ default を使う(~ は展開する)。"""
    return Path(os.environ.get(var) or default).expanduser()


VAULT = _envpath("VAULT", "~/Documents/Obsidian_Vault")
STUDY = VAULT / "Study"
MODEL_ID = 1980517307
MIN_BACK_CHARS = 20  # 裏がこれ未満のカードは捨てる(装飾を除いた実質文字数)

# --- LLM(能動的想起カード生成)---
OLLAMA_URL = "http://localhost:11434/api/generate"
LLM_MODEL = "gpt-oss:20b"        # 分類より重い「問題を作る」タスクなので精度重視の gpt-oss
# Ollama の num_ctx 既定は 4096。長いノートだとプロンプトだけで 3500 トークン超を食い、
# 残りが数百しかない。gpt-oss は reasoning モデルなのでその数百を thinking 側で使い切り、
# done_reason=length で打ち切られて response が【空】で返る(= カード0枚)。thinking には
# 答えが書けているので気づきにくい。明示的に広げること(2026-07-29)。
LLM_NUM_CTX = 16384
LLM_CHUNK_CHARS = 3500           # 1回の呼び出しに渡す本文の上限。超える分は分割して複数回投げる
CACHE_DIR = _envpath("CLAUDE_DIR", "~/.claude") / "scripts/.vault-anki-cache"
LLM_PROMPT = """あなたは学習用フラッシュカードの作成者です。以下の学習ノートから、\
能動的想起(active recall)に使えるカードを日本語で作ってください。

最重要規則 — **各カードは単独で成立させる**:
- カードを引く人はノートを見ていない。**質問文だけで答えが一意に定まる**こと。
- 「与えられた」「その例で」「上の式」「この問題」のような、カード外の文脈を指す表現は禁止。
- ノートが計算例(問題+解答)のときは、2通りの作り方がある:
  (a) 前提を質問に埋め込む — 例: 「正弦波 $y=3.00\\sin(\\frac{{\\pi}}{{0.100}}t-6.00\\pi x)$ [m] の周期 $T$ は?」
  (b) 一般知識として問う — 例: 「角振動数 $\\omega$ と周期 $T$ の関係式は?」
  前提が長すぎて埋め込めない事実は、カードにしない。
- 出力前に各カードを自己点検し、質問だけで答えられないものは捨てる。

その他の規則:
- 質問は**具体的に一問一答**(「○○の定義は?」「○○の公式は?」「なぜ○○か?」)。\
「○○について説明せよ」のような曖昧な質問は禁止。
- 答えは値・式・要点のみ簡潔に(1〜2文)。「A. 」「B. 」のような選択肢ラベルは使わず、答えそのものを書く。
- **数式・記号・LaTeX(\\frac, \\dot, 変数など)は必ず $...$ で囲む**。裸で書かない。
- 覚える価値のある事実・公式・手順・理由だけをカードにする。目次や見出しだけの箇所は無視。
- 6〜12枚程度。

出力は**次の形式だけ**。各カードを1つの `Q:` 行と1つの `A:` 行で書き、カード間は空行で区切る。\
JSON・箇条書き記号・コードフェンス・前置きは一切書かない。数式のバックスラッシュはそのまま。

Q: (質問)
A: (答え)

Q: (質問)
A: (答え)

# ノート「{title}」
{body}"""

# プロンプトを変えたらキャッシュを無効化するための版数
PROMPT_VER = 3  # 3: num_ctx 明示 + 本文分割。既存キャッシュは冒頭4000字だけの不完全版なので破棄

# 自己完結していない質問を落とす安全網(プロンプトで禁止しても漏れることがある)
CONTEXT_REF_RE = re.compile(
    r"与えられた|その例|この例|上記|上の式|前述|先ほど|本文|ノート(で|の|に)|この問題|同じ問題")

MATHJAX_SCRIPT = (
    '<script type="text/javascript">'
    'if(typeof MathJax==="undefined"){'
    'var s=document.createElement("script");'
    's.type="text/javascript";'
    's.src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-mml-chtml.min.js";'
    'document.head.appendChild(s);'
    '}'
    '</script>'
)

MODEL = genanki.Model(
    MODEL_ID,
    "Vault Study (MathJax)",
    fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Source"}],
    templates=[{
        "name": "Card",
        "qfmt": '<div class="front">{{Front}}</div>' + MATHJAX_SCRIPT,
        "afmt": '{{FrontSide}}<hr id="answer"><div class="back">{{Back}}</div>'
                '<div class="src">{{Source}}</div>',
    }],
    css=""".card{font-family:-apple-system,sans-serif;font-size:18px;
text-align:left;line-height:1.6;padding:16px;
color:#222;background:#fff}
.nightMode .card,.nightMode.card{color:#e0e0e0;background:#1e1e1e}
.front{font-size:20px;font-weight:700}
.src{margin-top:12px;font-size:12px;color:#999}
.nightMode .src{color:#777}
hr#answer{margin:14px 0}
ul,ol{margin:6px 0 6px 1.2em}
code{padding:1px 4px;border-radius:3px;background:#f0f0f0}
.nightMode code{background:#333;color:#e0e0e0}""",
)


def stable_id(text: str) -> int:
    """文字列から安定した正の int を作る(deck_id 用。hash() は毎回変わるので使わない)。"""
    return int(hashlib.sha1(text.encode()).hexdigest()[:12], 16) % (10**9) + 10**9


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip("\"'")
    # tags は次行以降の "- xxx" も拾う
    tags = re.findall(r"tags:\s*\n((?:\s*-\s*\S+\n)+)", text[:end + 4])
    if tags:
        fm["_tags"] = re.findall(r"-\s*(\S+)", tags[0])
    return fm, text[end + 4:]


def subject_of(fm: dict) -> str:
    for t in fm.get("_tags", []):
        if t.startswith("study/"):
            return t.split("/", 1)[1]
    return "未分類"


def clean_inline(s: str) -> str:
    """カードに出したくない Obsidian 記法を落とす。[[A|B]]→B、[[A]]→A。"""
    s = re.sub(r"\[\[([^\]\|]+)\|([^\]]+)\]\]", r"\2", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    return s


def _convert_math(text: str) -> str:
    """$…$ / $$…$$ を MathJax の \\(…\\) / \\[…\\] に変換する。
    数式内の < > & を HTML エンティティにエスケープする。"""
    store: list[tuple[str, bool]] = []

    def stash(m, disp):
        store.append((m.group(1), disp))
        return f"@@MATH{len(store) - 1}@@"

    text = re.sub(r"\$\$(.+?)\$\$", lambda m: stash(m, True), text, flags=re.DOTALL)
    text = re.sub(r"\$(.+?)\$", lambda m: stash(m, False), text)
    return text, store


def _restore_math(html: str, store: list[tuple[str, bool]]) -> str:
    """プレースホルダを MathJax 記法に戻す。"""
    for i, (content, disp) in enumerate(store):
        esc = htmllib.escape(content, quote=False)
        wrapped = f"\\[{esc}\\]" if disp else f"\\({esc}\\)"
        html = html.replace(f"@@MATH{i}@@", wrapped)
    return html


def to_html(body: str) -> str:
    """Markdown→HTML(裏面用)。数式はプレースホルダで保護してから戻す。"""
    body, store = _convert_math(body)
    html = md.markdown(clean_inline(body), extensions=["extra", "sane_lists"])
    return _restore_math(html, store)


def front_to_html(heading: str) -> str:
    """見出しテキスト→表面用HTML。

    markdown の to_html() を通すと「1. 変数分離法」のような番号付き見出しが
    <ol><li> に変換されてしまい、Anki 上で問題文が見えなくなる。
    表面は見出しテキストそのままを出せばよいので、数式変換と最低限のインライン
    装飾だけを行う軽量変換にする。"""
    text = clean_inline(heading)
    text, store = _convert_math(text)
    # 最低限のインライン装飾: **bold** → <strong>, *italic* → <em>, `code` → <code>
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = _restore_math(text, store)
    return text


HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)
# 見出しテキスト先頭の番号("1. " "2. " 等)を除去する正規表現
HEADING_NUM_RE = re.compile(r"^\d+\.\s+")
DECORATION_RE = re.compile(r"[#*`>\-\[\]!|_~\s$\\{}()]")


def split_cards(title: str, body: str) -> list[tuple[str, str]]:
    """(表, 裏マークダウン) のリスト。見出しごとに割り、空同然は捨てる。"""
    matches = list(HEADING_RE.finditer(body))
    cards = []
    if not matches:
        if len(DECORATION_RE.sub("", body)) >= MIN_BACK_CHARS:
            cards.append((title or "(無題)", body.strip()))
        return cards
    for i, m in enumerate(matches):
        head = m.group(2).strip()
        # 先頭の番号("1. " 等)を除去 — markdown に渡すとリストと誤認されるため
        head = HEADING_NUM_RE.sub("", head)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        back = body[start:end].strip()
        if len(DECORATION_RE.sub("", back)) >= MIN_BACK_CHARS:
            cards.append((head, back))
    return cards


def strip_for_llm(body: str) -> str:
    """LLM に渡す前に、画像埋め込み・添付参照など無関係なノイズを落とす。"""
    body = re.sub(r"!\[\[[^\]]*\]\]", "", body)      # ![[image.png]]
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)  # ![](path)
    return body.strip()


def _parse_qa(text: str) -> list[tuple[str, str]]:
    """`Q:` 行と `A:` 行の組を拾う。LaTeX のバックスラッシュはそのまま通る(JSON と違い壊れない)。"""
    out, q = [], None
    for line in text.splitlines():
        s = line.strip()
        if m := re.match(r"^Q[:：]\s*(.+)", s):
            q = m.group(1).strip()
        elif (m := re.match(r"^A[:：]\s*(.+)", s)) and q:
            out.append((q, m.group(1).strip()))
            q = None
    return out


def _chunks(body: str, size: int = LLM_CHUNK_CHARS) -> list[str]:
    """本文を見出し境界優先で分割する。以前は body[:4000] で切り捨てていたため、
    長いノートは冒頭しかカードにならなかった(残りは黙って消えていた)。"""
    if len(body) <= size:
        return [body]
    merged, cur = [], ""
    for block in re.split(r"(?m)^(?=#{1,4} )", body):
        if cur and len(cur) + len(block) > size:
            merged.append(cur)
            cur = block
        else:
            cur += block
    if cur.strip():
        merged.append(cur)
    out = []                      # 見出しの無い巨大ブロックは素朴に切る
    for c in merged:
        while len(c) > size:
            out.append(c[:size])
            c = c[size:]
        if c.strip():
            out.append(c)
    return out


def _ask_one(prompt: str, attempt: int) -> str:
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps({"model": LLM_MODEL, "prompt": prompt, "stream": False,
                         "options": {"temperature": 0.3 + 0.1 * attempt,
                                     "num_ctx": LLM_NUM_CTX}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as res:
        return json.loads(res.read())["response"]


def _ask_llm(title: str, body: str, retries: int = 3) -> list[tuple[str, str]]:
    """gpt-oss(reasoning モデル)はたまに空/解析不能を返すので、空なら数回やり直す。
    長いノートは分割して複数回投げ、質問文で重複を落として束ねる。"""
    parts = _chunks(body)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for i, chunk in enumerate(parts, 1):
        if len(parts) > 1:
            print(f"      ({i}/{len(parts)})", file=sys.stderr)
        prompt = LLM_PROMPT.format(title=title, body=chunk)
        for attempt in range(retries):
            try:
                resp = _ask_one(prompt, attempt)
            except Exception as e:  # noqa: BLE001 — 1チャンクの失敗でノート全体を捨てない。直後に stderr へ出す
                print(f"      ! {type(e).__name__} — このチャンクを飛ばす", file=sys.stderr)
                break
            if cards := _parse_qa(resp):
                for q, a in cards:
                    if q not in seen:
                        seen.add(q)
                        out.append((q, a))
                break
            if attempt + 1 < retries:
                print(f"      (空応答 → 再試行 {attempt + 2}/{retries})", file=sys.stderr)
    return out


def llm_cards(path: Path, fm: dict, body: str, title: str) -> list[tuple[str, str]]:
    """gpt-oss で能動的想起カード(質問→答え)を作る。ノート単位でキャッシュし、
    未変更なら LLM を呼ばない(guid も安定する)。プロンプト改版時は PROMPT_VER でキャッシュ無効。
    長いノートは _chunks で分割して複数回投げるので、冒頭だけで打ち切られることはない。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    uid = fm.get("uid", path.stem)
    cache = CACHE_DIR / f"{uid}.json"
    mtime = path.stat().st_mtime
    if cache.exists():
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("mtime") == mtime and c.get("ver") == PROMPT_VER:
            return [(x["q"], x["a"]) for x in c["cards"]]

    clean = strip_for_llm(body)
    if len(DECORATION_RE.sub("", clean)) < MIN_BACK_CHARS:
        return []
    print(f"    (LLM生成中… {path.name})", file=sys.stderr)
    try:
        cards = _ask_llm(title, clean)
    except Exception as e:  # noqa: BLE001 — タイムアウト等。1ノートの失敗で全体を殺さない。直後に stderr へ出す
        print(f"    ! LLM失敗({type(e).__name__}): {path.name} — スキップ(キャッシュせず次回再試行)",
              file=sys.stderr)
        return []
    # 安全網: 文脈参照つき(=カード単独で答えられない)質問を捨てる
    kept = [(q, a) for q, a in cards if not CONTEXT_REF_RE.search(q)]
    if dropped := len(cards) - len(kept):
        print(f"    (自己完結でない {dropped} 枚を破棄: {path.name})", file=sys.stderr)
    # 空(=一過性の解析失敗の可能性)はキャッシュしない。次回再試行させる
    if kept:
        cache.write_text(
            json.dumps({"mtime": mtime, "ver": PROMPT_VER,
                        "cards": [{"q": q, "a": a} for q, a in kept]},
                       ensure_ascii=False), encoding="utf-8")
    else:
        print(f"    ! 0枚(キャッシュせず次回再試行): {path.name}", file=sys.stderr)
    return kept


def notes_from_args(args) -> list[Path]:
    if args.all:
        return sorted(STUDY.glob("*.md"))
    if args.tag:
        out = []
        for p in sorted(STUDY.glob("*.md")):
            fm, _ = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            if args.tag.lstrip("#") in fm.get("_tags", []):
                out.append(p)
        return out
    return [Path(f) for f in args.files]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="Study のノート .md")
    ap.add_argument("--tag", help="この study/xxx タグの全ノート")
    ap.add_argument("--all", action="store_true", help="Study/ の全ノート")
    ap.add_argument("-o", "--output", help="出力 .apkg のパス")
    ap.add_argument("--llm", action="store_true",
                    help="gpt-oss で能動的想起カード(質問→答え)を生成。見出し分割より高品質だが遅い")
    args = ap.parse_args()

    paths = notes_from_args(args)
    if not paths:
        print("対象ノートがない。ファイル / --tag / --all のいずれかを指定する。", file=sys.stderr)
        sys.exit(1)

    decks: dict[str, genanki.Deck] = {}
    n_cards = 0
    for p in paths:
        if not p.exists():
            print(f"  ! ない: {p}", file=sys.stderr)
            continue
        fm, body = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        title = fm.get("title") or p.stem
        subject = subject_of(fm)
        cards = llm_cards(p, fm, body, title) if args.llm else split_cards(title, body)
        if not cards:
            print(f"  - スキップ(カードなし): {p.name}")
            continue

        deck_name = f"Study::{subject}"
        if deck_name not in decks:
            decks[deck_name] = genanki.Deck(stable_id(deck_name), deck_name)
        deck = decks[deck_name]
        anki_tag = re.sub(r"\s+", "_", title)
        for head, back in cards:
            note = genanki.Note(
                model=MODEL,
                fields=[front_to_html(head), to_html(back), title],
                tags=[subject, anki_tag],
                guid=genanki.guid_for(fm.get("uid", p.stem), head),
            )
            deck.add_note(note)
        n_cards += len(cards)
        print(f"  {p.name} → {deck_name}({len(cards)} 枚)")

    if not n_cards:
        print("生成できるカードがなかった。")
        return

    out = Path(args.output).expanduser() if args.output else \
        Path.home() / "Downloads" / f"vault-anki-{dt.datetime.now():%Y%m%d}.apkg"
    genanki.Package(list(decks.values())).write_to_file(out)
    print(f"\n書き出し: {out}({n_cards} 枚 / {len(decks)} デッキ)。ダブルクリックで Anki に取り込む。")


if __name__ == "__main__":
    main()
