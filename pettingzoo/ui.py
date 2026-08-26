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
:root{
  --bg:#0e1116; --panel:#161b22; --panel2:#1c2230; --line:#2a3140;
  --ink:#e6edf3; --dim:#8b98a9; --accent:#4da3ff; --good:#3fb950;
  --warn:#d29922; --bad:#f85149;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}
.stApp{background:var(--bg)}
#MainMenu,footer,header[data-testid="stHeader"]{visibility:hidden;height:0}
.block-container{padding-top:1.2rem;padding-bottom:2rem;max-width:1500px}
section[data-testid="stSidebar"]{background:var(--panel);border-right:1px solid var(--line)}
section[data-testid="stSidebar"] .block-container{padding-top:1rem}

/* tabs */
button[data-baseweb="tab"]{color:var(--dim)!important;font-weight:500}
button[data-baseweb="tab"][aria-selected="true"]{color:var(--accent)!important}
div[data-baseweb="tab-highlight"]{background:var(--accent)!important}
div[data-baseweb="tab-border"]{background:var(--line)!important}

/* cards */
.pz-card{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:15px 16px;margin-bottom:15px}
.pz-card h2{font-size:12px;margin:0 0 12px;text-transform:uppercase;
  letter-spacing:.8px;color:var(--dim);font-weight:650}
.pz-stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.pz-stat{flex:1;min-width:150px;background:var(--panel2);border:1px solid var(--line);
  border-radius:9px;padding:11px 14px}
.pz-stat .lbl{color:var(--dim);font-size:10.5px;text-transform:uppercase;
  letter-spacing:.6px;margin-bottom:2px}
.pz-stat .val{font-size:25px;font-weight:700;font-family:var(--mono);line-height:1.15}
.pz-stat .sub{color:var(--dim);font-size:11.5px;margin-top:2px}
.pz-stat.hot{border-color:var(--accent)}
.pz-stat.hot .val{color:var(--accent)}

/* tables */
table.pz{width:100%;border-collapse:collapse;font-size:13px}
table.pz th{text-align:left;color:var(--dim);font-weight:600;font-size:10.5px;
  text-transform:uppercase;letter-spacing:.5px;padding:7px 9px;
  border-bottom:1px solid var(--line);white-space:nowrap}
table.pz td{padding:7px 9px;border-bottom:1px solid #1e242e;color:var(--ink)}
table.pz tr:last-child td{border-bottom:none}
table.pz tr:hover td{background:#1a2130}
table.pz td.num,table.pz th.num{text-align:right;font-family:var(--mono);
  font-variant-numeric:tabular-nums}
table.pz tr.me td{background:#12243a!important;box-shadow:inset 3px 0 0 var(--accent)}
.pz-scroll{max-height:520px;overflow:auto;border-radius:8px}

/* bits */
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:10px;
  font-weight:700;letter-spacing:.3px}
.QB{background:#3b2a4d;color:#c9a5ee}.RB{background:#123528;color:#5fd8a0}
.WR{background:#123044;color:#6fbcf5}.TE{background:#3f2f16;color:#e8bb63}
.K{background:#2a2f38;color:#a9b6c6}.DST{background:#37232a;color:#f08d8d}
.flag{color:var(--bad);font-weight:650;font-size:10.5px}
.flagq{color:var(--warn);font-weight:650;font-size:10.5px}
.good{color:var(--good)}.bad{color:var(--bad)}.dim{color:var(--dim)}
.bar{height:7px;background:var(--panel2);border-radius:4px;overflow:hidden;min-width:56px}
.bar>i{display:block;height:100%;background:var(--accent)}
.pz-hint{color:var(--dim);font-size:11.5px;line-height:1.6;margin-top:9px}
.pz-why{background:var(--panel2);border:1px solid var(--line);
  border-left:3px solid var(--accent);border-radius:9px;padding:12px 15px;margin-bottom:14px}
.pz-why div{margin:5px 0;font-size:13.5px}
.pz-run{background:#3a2a12;border:1px solid #6b4d13;border-radius:8px;
  padding:9px 13px;color:#e8bb63;font-size:12.5px;margin-top:10px}
.tag{font-family:var(--mono);font-size:11px;color:var(--dim)}

/* tame the data_editor so the board matches */
div[data-testid="stDataFrame"],div[data-testid="stDataEditor"]{
  border:1px solid var(--line);border-radius:9px}
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
