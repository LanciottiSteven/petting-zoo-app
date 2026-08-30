"""
Shared presentation layer for the Streamlit build.

Streamlit's stock widgets look nothing like the local app, so the design tokens
and table renderers from `web/index.html` are ported here and emitted as raw
HTML. Interactive pieces (the draft board checkboxes, buttons, selects) stay as
real Streamlit widgets; everything read-only is rendered by these helpers so
both apps look the same.
"""
from __future__ import annotations

CSS = """
<style>
/*
  Surfaces were near-black (#06080a) and read as a void. Lifted the whole ladder:
  page #1e242e, card #2e3743 — card/plane 1.29, border/card 1.49, body ink 10.7:1.
  A lighter card costs contrast, so every hue below was re-derived against it
  rather than carried over: the position chips were brightened and re-validated
  (normal-vision dE 17.3, CVD 7.2 — the 6-8 band is legal because every chip is
  text-labelled). Chip tint dropped 16% -> 10% to buy back chip contrast (4.86:1).
*/
:root{
  --plane:#1e242e; --card:#2e3743; --inset:#3a4553; --hover:#39434f;
  --line:#46515f; --line-soft:#3b4552;
  --ink:#eef2f7; --ink-2:#b4c1d0; --ink-3:#9fadbe;
  --accent:#5aa2ff; --good:#4bd66b; --warn:#f5bb2e; --bad:#ff7b78;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --r:12px;
}
.stApp{background:var(--plane);color:var(--ink)}
#MainMenu,footer,header[data-testid="stHeader"]{visibility:hidden;height:0}
.block-container{padding-top:1.1rem;padding-bottom:2.4rem;max-width:1560px}
section[data-testid="stSidebar"]{background:var(--card);border-right:1px solid var(--line)}
section[data-testid="stSidebar"] .block-container{padding-top:1rem}
/* Set ink once and let it inherit. A blanket `.stApp span{color:...}` rule
   (specificity 0,1,1) silently beats every semantic class below it. Don't. */
.stApp{color:var(--ink)}
.stMarkdown,.stMarkdown p{color:var(--ink)}

button[data-baseweb="tab"]{color:var(--ink-2)!important;font-weight:500}
button[data-baseweb="tab"][aria-selected="true"]{color:var(--accent)!important}
div[data-baseweb="tab-highlight"]{background:var(--accent)!important}
div[data-baseweb="tab-border"]{background:var(--line-soft)!important}

.pz-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);
  padding:18px 20px;margin-bottom:16px}
.pz-card h2,.pz-h{font-size:11.5px;margin:0 0 14px;text-transform:uppercase;
  letter-spacing:.9px;color:var(--ink-2);font-weight:650;display:block}
.pz-h{margin-top:16px}
.pz-stats{display:flex;gap:13px;flex-wrap:wrap;margin-bottom:16px}
.pz-stat{flex:1;min-width:162px;background:var(--card);border:1px solid var(--line);
  border-radius:var(--r);padding:14px 17px}
.pz-stat .lbl{color:var(--ink-2);font-size:10.5px;text-transform:uppercase;
  letter-spacing:.7px;margin-bottom:4px}
.pz-stat .val{font-size:27px;font-weight:700;font-family:var(--mono);
  line-height:1.15;color:var(--ink)}
.pz-stat .sub{color:var(--ink-3);font-size:11.5px;margin-top:4px}
.pz-stat.hot{border-color:var(--accent);background:linear-gradient(
  180deg,rgba(90,162,255,.13),rgba(90,162,255,0) 72%),var(--card)}
.pz-stat.hot .val{color:var(--accent)}

/* ---- tables: roomier rows, quieter rules, zebra to track across wide rows ---- */
/* Wide tables SCROLL rather than squeeze. Without overflow-x the extra row
   padding just forces player names to wrap onto two lines and shoves the last
   column off-screen, which reads worse than the cramped version it replaced. */
.pz-tw{border:1px solid var(--line-soft);border-radius:10px;overflow-x:auto;
  background:var(--card)}
.pz-tw.scroll{max-height:560px;overflow-y:auto}
.pz-tw::-webkit-scrollbar{height:9px;width:9px}
.pz-tw::-webkit-scrollbar-thumb{background:var(--line);border-radius:5px}
.pz-tw::-webkit-scrollbar-track{background:transparent}
table.pz{width:100%;border-collapse:separate;border-spacing:0;font-size:13.5px;
  line-height:1.45}
table.pz th{text-align:left;color:var(--ink-2);font-weight:650;font-size:10.5px;
  text-transform:uppercase;letter-spacing:.7px;padding:11px 14px;white-space:nowrap;
  background:var(--inset);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:1}
table.pz td{padding:11px 14px;border-bottom:1px solid var(--line-soft);
  color:var(--ink);vertical-align:middle;white-space:nowrap}
/* Cells hold one line by default — the table scrolls on x, so wrapping just
   makes row heights ragged. Opt in with .wrap for prose columns. */
table.pz td.wrap{white-space:normal;min-width:230px}
/* research dossier: let long labels and source names wrap so the value column
   stays inside its grid track instead of being pushed out and clipped */
table.pz td.wrap-label{white-space:normal;min-width:0;line-height:1.35}
/* the source caption sits inside a numeric cell, which is nowrap — let it wrap
   so it is never clipped at the edge of a narrow grid track */
table.pz td.num .tag{white-space:normal;display:block;line-height:1.3}
table.pz tbody tr:last-child td{border-bottom:none}
table.pz tbody tr:nth-child(even) td{background:rgba(255,255,255,.022)}
table.pz tbody tr:hover td{background:var(--hover)}
table.pz td:first-child,table.pz th:first-child{padding-left:18px}
table.pz td:last-child,table.pz th:last-child{padding-right:18px}
table.pz td.num,table.pz th.num{text-align:right;font-family:var(--mono);
  font-variant-numeric:tabular-nums;white-space:nowrap}
table.pz tr.me td{background:rgba(90,162,255,.16)!important;
  box-shadow:inset 3px 0 0 var(--accent)}
table.pz tr.me:hover td{background:rgba(90,162,255,.21)!important}

.pill{display:inline-block;padding:2.5px 10px;border-radius:20px;font-size:10.5px;
  font-weight:700;letter-spacing:.4px;border:1px solid transparent;white-space:nowrap}
.pill.QB{color:#f497d7!important;background:#424152;border-color:#69546f}
.pill.RB{color:#4bcf8e!important;background:#31464a;border-color:#37655a}
.pill.WR{color:#5fc3fb!important;background:#334555;border-color:#3d617a}
.pill.TE{color:#f8ad04!important;background:#42433d;border-color:#6b5a30}
.pill.K,.pill.DST{color:#b1bfd0!important;background:#3b4551;border-color:#55606d}

.pz-card .flag,.flag{color:var(--bad)!important;font-weight:650;font-size:10.5px}
.pz-card .flagq,.flagq{color:var(--warn)!important;font-weight:650;font-size:10.5px}
.good{color:var(--good)!important}
.bad{color:var(--bad)!important}
.dim{color:var(--ink-3)!important}
.bar{height:8px;background:var(--inset);border-radius:5px;overflow:hidden;min-width:60px}
.bar>i{display:block;height:100%;background:var(--accent);border-radius:5px}
.pz-hint{color:var(--ink-3);font-size:11.5px;line-height:1.7;margin-top:12px}
.pz-hint b{color:var(--ink-2)}
.pz-why{background:var(--inset);border:1px solid var(--line);
  border-left:3px solid var(--accent);border-radius:10px;padding:15px 18px;margin-bottom:16px}
.pz-why div{margin:7px 0;font-size:13.5px;line-height:1.6;color:var(--ink)}
.pz-run{background:rgba(245,187,46,.12);border:1px solid rgba(245,187,46,.4);
  border-radius:9px;padding:11px 15px;color:var(--warn);font-size:12.5px;margin-top:12px}
.tag{font-family:var(--mono);font-size:11px;color:var(--ink-3)!important}

.pz-brand{display:flex;justify-content:center;margin:0 0 10px}
.pz-brand img{width:88%;max-width:210px;height:auto;
  filter:drop-shadow(0 3px 10px rgba(0,0,0,.45))}

/* NEVER put overflow:hidden here. The board is a canvas grid that scrolls
   itself; clipping it lops off the right-hand columns (Flag was cut off). */
div[data-testid="stDataFrame"],div[data-testid="stDataEditor"]{
  border:1px solid var(--line);border-radius:10px}

/* Streamlit paints its own widgets from config.toml. That file is easy to lose
   (a `cp *` skips dot-directories), and when it is missing the stock red
   #FF4B4B shows up on toggles and sliders while everything else stays blue.
   Force the accent here so the app themes itself regardless. */
[data-testid="stCheckbox"] label[data-selected="true"] > div:not([data-testid]){
  background:var(--accent)!important}
/* active tab: label text and the underline indicator */
[data-testid="stTab"][data-selected="true"],
[data-testid="stTab"][data-selected="true"] *{color:var(--accent)!important}
/* Scope the indicator to the TAB, not the tab strip. `[data-testid="stTabs"]
   div[data-rac]` matched half the page and painted the content area blue. */
[data-testid="stTab"] > div[data-rac]{background:var(--accent)!important}
/* slider thumb + the readout above it */
[data-testid="stSlider"] div[data-rac][style*="position: absolute"]{
  background:var(--accent)!important}
[data-testid="stSliderThumbValue"]{color:var(--accent)!important}
[data-testid="stRadioOption"] [data-selected="true"] > div,
[data-testid="stRadioOption"] input:checked + div{background:var(--accent)!important}
[data-testid="stBaseButton-primaryFormSubmit"]{
  background:var(--accent)!important;border-color:var(--accent)!important;
  color:#0b1b2e!important}
[data-testid="stSlider"] [data-testid="stThumbValue"]{color:var(--accent)!important}
/* The slider's filled track is deliberately left alone. Its gradient comes
   from an emotion class with value-dependent stops, so it cannot be restated
   in CSS, hue-rotate() lands on the wrong colour (browsers filter in a
   different colour space than the spec matrix), and forcing a flat background
   turns a hairline track into a block — and that rule would apply even when
   config.toml IS present, breaking the good case to patch the bad one.
   config.toml paints it correctly; ship that file. */
progress::-webkit-progress-value,[role="progressbar"]>div{background:var(--accent)!important}

/* widget labels and placeholders were the hardest text to read */
[data-testid="stWidgetLabel"],[data-testid="stWidgetLabel"] p{
  color:var(--ink-2)!important;font-size:12.5px}
input::placeholder,textarea::placeholder{color:var(--ink-3)!important;opacity:1}
[data-baseweb="select"] [aria-hidden="true"]{color:var(--ink-2)!important}
div[data-baseweb="select"]>div,div[data-baseweb="input"]>div,
.stTextInput input,.stNumberInput input{
  background:var(--inset)!important;border-color:var(--line)!important;
  color:var(--ink)!important}
div[data-testid="stExpander"]{background:var(--card);border:1px solid var(--line);
  border-radius:10px}
div[data-testid="stExpander"] summary{color:var(--ink-2)!important}
.stSlider [data-baseweb="slider"] div[role="slider"]{background:var(--accent)!important}
button[kind="primary"]{background:var(--accent)!important;border-color:var(--accent)!important;
  color:#0b1b2e!important;font-weight:650}
button[kind="secondary"]{background:var(--inset)!important;border-color:var(--line)!important;
  color:var(--ink)!important}
button[kind="secondary"]:hover{border-color:var(--accent)!important;color:var(--accent)!important}
hr{border-color:var(--line-soft)!important}
</style>
"""

POS_CLS = {"D/ST": "DST"}


def pill(pos: str, suffix: str = "") -> str:
    return f'<span class="pill {POS_CLS.get(pos, pos)}">{pos}{suffix}</span>'


def flag_html(flag: str | None, games_missed: int = 0) -> str:
    if games_missed:
        return f' <span class="flag">OUT {games_missed}G</span>'
    if not flag:
        return ""
    soft = flag.lower().startswith(("question", "doubt"))
    return f' <span class="{"flagq" if soft else "flag"}">{flag}</span>'


def num(v, d: int = 1, dash: str = "—") -> str:
    return dash if v is None else f"{float(v):.{d}f}"


def bar(frac: float) -> str:
    w = max(0.0, min(1.0, frac)) * 100
    return f'<div class="bar"><i style="width:{w:.0f}%"></i></div>'


def stats(items) -> str:
    """items: list of (label, value, sub, highlight)"""
    out = []
    for label, value, sub, hot in items:
        out.append(f'<div class="pz-stat{" hot" if hot else ""}">'
                   f'<div class="lbl">{label}</div><div class="val">{value}</div>'
                   f'<div class="sub">{sub or "&nbsp;"}</div></div>')
    return f'<div class="pz-stats">{"".join(out)}</div>'


def table(headers, rows, scroll: bool = False) -> str:
    """headers: list of (label, is_numeric). rows: list of (cells, is_me)."""
    head = "".join(f'<th class="{"num" if n else ""}">{h}</th>' for h, n in headers)
    body = []
    for cells, me in rows:
        tds = "".join(cells)
        body.append(f'<tr class="{"me" if me else ""}">{tds}</tr>')
    html = (f'<table class="pz"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')
    cls = "pz-tw scroll" if scroll else "pz-tw"
    return f'<div class="{cls}">{html}</div>'


def td(v, cls: str = "") -> str:
    return f'<td class="{cls}">{v}</td>'


def md(text: str) -> str:
    """Convert **bold** for every pair, not just the first one."""
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def cols(items, min_px: int = 300) -> str:
    """
    Responsive columns for the research blocks. A flex row with width:100%
    tables inside collapses and the columns overlap; grid with a hard minmax
    keeps each block in its own track and wraps cleanly instead.
    """
    inner = "".join(f'<div style="min-width:0">{c}</div>' for c in items)
    return (f'<div style="display:grid;gap:18px 22px;'
            f'grid-template-columns:repeat(auto-fit,minmax({min_px}px,1fr))">'
            f'{inner}</div>')


def card(title: str, body: str, hint: str = "") -> str:
    h = f'<div class="pz-hint">{hint}</div>' if hint else ""
    t = f"<h2>{title}</h2>" if title else ""
    return f'<div class="pz-card">{t}{body}{h}</div>'
