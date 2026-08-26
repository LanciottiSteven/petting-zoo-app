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
  Palette notes — this was validated, not eyeballed:
  * Surfaces step 1.30 (card over page) and 1.49 (border over card). The old
    scheme sat at 1.09, which is why everything read as flat mud.
  * Position hues are a colour-blind-checked set: worst normal-vision dE 16.7
    (floor 15) and worst CVD dE 7.3. A CVD score in the 6-8 band is only legal
    with secondary encoding — every pill prints its position as text, so hue
    never carries identity on its own. The previous blue/violet pair measured
    dE 9.8 and was genuinely hard to tell apart.
  * K and D/ST deliberately share one neutral. Both are streaming slots the app
    tells you not to spend on; giving them colour would over-signal them.
*/
:root{
  --plane:#06080a; --card:#1e2530; --inset:#28313d; --hover:#2b3542;
  --line:#36414f; --line-soft:#2a3340;
  --ink:#e9eef4; --ink-2:#9dabbb; --ink-3:#71808f;
  --accent:#4c9aff; --good:#3fcf60; --warn:#fab219; --bad:#f0605d;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}
.stApp{background:var(--plane);color:var(--ink)}
#MainMenu,footer,header[data-testid="stHeader"]{visibility:hidden;height:0}
.block-container{padding-top:1.1rem;padding-bottom:2rem;max-width:1560px}
section[data-testid="stSidebar"]{background:var(--card);border-right:1px solid var(--line)}
section[data-testid="stSidebar"] .block-container{padding-top:1rem}
/* Set ink once and let it inherit. A blanket `.stApp span{color:...}` rule
   (specificity 0,1,1) silently beats every semantic class below it — .flagq,
   .bad, .dim and the position chips all render as plain ink. Don't reintroduce it. */
.stApp{color:var(--ink)}
.stMarkdown,.stMarkdown p{color:var(--ink)}

/* tabs */
button[data-baseweb="tab"]{color:var(--ink-2)!important;font-weight:500}
button[data-baseweb="tab"][aria-selected="true"]{color:var(--accent)!important}
div[data-baseweb="tab-highlight"]{background:var(--accent)!important}
div[data-baseweb="tab-border"]{background:var(--line-soft)!important}

/* cards */
.pz-card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:16px 17px;margin-bottom:15px}
.pz-card h2{font-size:11.5px;margin:0 0 12px;text-transform:uppercase;
  letter-spacing:.9px;color:var(--ink-2);font-weight:650}
.pz-stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.pz-stat{flex:1;min-width:158px;background:var(--card);border:1px solid var(--line);
  border-radius:11px;padding:12px 15px}
.pz-stat .lbl{color:var(--ink-2);font-size:10.5px;text-transform:uppercase;
  letter-spacing:.7px;margin-bottom:3px}
.pz-stat .val{font-size:26px;font-weight:700;font-family:var(--mono);
  line-height:1.15;color:var(--ink)}
.pz-stat .sub{color:var(--ink-3);font-size:11.5px;margin-top:3px}
.pz-stat.hot{border-color:var(--accent);background:linear-gradient(
  180deg,rgba(76,154,255,.10),rgba(76,154,255,0) 70%),var(--card)}
.pz-stat.hot .val{color:var(--accent)}

/* tables */
table.pz{width:100%;border-collapse:collapse;font-size:13px}
table.pz th{text-align:left;color:var(--ink-2);font-weight:600;font-size:10.5px;
  text-transform:uppercase;letter-spacing:.6px;padding:8px 10px;
  border-bottom:1px solid var(--line);white-space:nowrap;background:var(--card)}
table.pz td{padding:8px 10px;border-bottom:1px solid var(--line-soft);color:var(--ink)}
table.pz tr:last-child td{border-bottom:none}
table.pz tbody tr:hover td{background:var(--hover)}
table.pz td.num,table.pz th.num{text-align:right;font-family:var(--mono);
  font-variant-numeric:tabular-nums}
table.pz tr.me td{background:rgba(76,154,255,.12)!important;
  box-shadow:inset 3px 0 0 var(--accent)}
.pz-scroll{max-height:520px;overflow:auto;border-radius:9px}

/* position chips — tint derived from each hue so lightness stays even */
.pill{display:inline-block;padding:1.5px 8px;border-radius:20px;font-size:10.5px;
  font-weight:700;letter-spacing:.3px;border:1px solid transparent}
.pill.QB{color:#d17fb8!important;background:#302e3e;border-color:#503e56}
.pill.RB{color:#3fb87c!important;background:#213438;border-color:#274e45}
.pill.WR{color:#56b4e9!important;background:#243342;border-color:#2e4d64}
.pill.TE{color:#e69f00!important;background:#32312b;border-color:#564723}
.pill.K,.pill.DST{color:#96a2b1!important;background:#2a323d;border-color:#404854}

.pz-card .flag,.flag{color:var(--bad)!important;font-weight:650;font-size:10.5px}
.pz-card .flagq,.flagq{color:var(--warn)!important;font-weight:650;font-size:10.5px}
.good{color:var(--good)!important}
.bad{color:var(--bad)!important}
.dim{color:var(--ink-3)!important}
.bar{height:7px;background:var(--inset);border-radius:4px;overflow:hidden;min-width:56px}
.bar>i{display:block;height:100%;background:var(--accent);border-radius:4px}
.pz-hint{color:var(--ink-3);font-size:11.5px;line-height:1.65;margin-top:10px}
.pz-hint b{color:var(--ink-2)}
.pz-why{background:var(--inset);border:1px solid var(--line);
  border-left:3px solid var(--accent);border-radius:10px;padding:13px 16px;margin-bottom:14px}
.pz-why div{margin:6px 0;font-size:13.5px;color:var(--ink)}
.pz-run{background:rgba(250,178,25,.10);border:1px solid rgba(250,178,25,.35);
  border-radius:9px;padding:10px 14px;color:var(--warn);font-size:12.5px;margin-top:11px}
.tag{font-family:var(--mono);font-size:11px;color:var(--ink-3)!important}

/* native Streamlit widgets, so the board and inputs sit on the same ladder */
div[data-testid="stDataFrame"],div[data-testid="stDataEditor"]{
  border:1px solid var(--line);border-radius:10px;overflow:hidden}
div[data-baseweb="select"]>div,div[data-baseweb="input"]>div,
.stTextInput input,.stNumberInput input{
  background:var(--inset)!important;border-color:var(--line)!important;
  color:var(--ink)!important}
div[data-testid="stExpander"]{background:var(--card);border:1px solid var(--line);
  border-radius:10px}
div[data-testid="stExpander"] summary{color:var(--ink-2)!important}
.stSlider [data-baseweb="slider"] div[role="slider"]{background:var(--accent)!important}
button[kind="primary"]{background:var(--accent)!important;border-color:var(--accent)!important;
  color:#06121f!important;font-weight:650}
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
    return f'<div class="pz-scroll">{html}</div>' if scroll else html


def td(v, cls: str = "") -> str:
    return f'<td class="{cls}">{v}</td>'


def card(title: str, body: str, hint: str = "") -> str:
    h = f'<div class="pz-hint">{hint}</div>' if hint else ""
    t = f"<h2>{title}</h2>" if title else ""
    return f'<div class="pz-card">{t}{body}{h}</div>'
