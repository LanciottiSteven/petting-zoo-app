"""PDF draft plan. Print-oriented: light ground, high contrast, no screen colours."""
from __future__ import annotations
import io, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, KeepTogether)

from .league import LEAGUE_NAME, MY_TEAM_NAME, N_TEAMS, ROSTER_SIZE, DRAFT_ORDER

INK = colors.HexColor("#14181f")
DIM = colors.HexColor("#5c6673")
RULE = colors.HexColor("#d4dae1")
BAND = colors.HexColor("#f2f5f8")
ACCENT = colors.HexColor("#1f6fd0")
POS_BG = {"QB": "#efe0f0", "RB": "#dcf0e4", "WR": "#dde9f7",
          "TE": "#f6ecd6", "K": "#ebedf0", "D/ST": "#f7e0e0"}


def _styles():
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Title"], fontSize=19, leading=23,
                             textColor=INK, alignment=0, spaceAfter=2),
        "sub": ParagraphStyle("sub", parent=ss["Normal"], fontSize=9.5, leading=13,
                              textColor=DIM),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11, leading=14,
                             textColor=INK, spaceBefore=13, spaceAfter=5),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9, leading=12.5,
                               textColor=INK),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8, leading=11,
                                textColor=DIM),
        "cell": ParagraphStyle("cell", parent=ss["Normal"], fontSize=8.5, leading=11,
                               textColor=INK),
        "cellдим": ParagraphStyle("celld", parent=ss["Normal"], fontSize=8, leading=10.5,
                                  textColor=DIM),
    }


def _pos_chip(pos: str) -> str:
    return (f'<font backColor="{POS_BG.get(pos, "#ebedf0")}"> <b>{pos}</b> </font>')


def build_pdf(plan: dict, pool_lookup=None, seat_label: str | None = None) -> bytes:
    S = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.62 * inch, rightMargin=0.62 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.55 * inch,
                            title=f"{LEAGUE_NAME} — draft plan",
                            author="Petting Zoo draft assistant")
    st = []
    slot = plan["my_slot"]
    who = seat_label or DRAFT_ORDER.get(slot, f"Seat {slot}")
    today = datetime.date.today().isoformat()

    st.append(Paragraph(f"{LEAGUE_NAME} — draft plan", S["h1"]))
    st.append(Paragraph(
        f"Seat {slot} · {who}"
        + (f" · {MY_TEAM_NAME}" if who == DRAFT_ORDER.get(slot) and slot == 5 else "")
        + f" &nbsp;|&nbsp; {N_TEAMS}-team full PPR, {ROSTER_SIZE}-man roster"
        f" &nbsp;|&nbsp; generated {today}", S["sub"]))
    st.append(Spacer(1, 9))

    shape = " · ".join(f"{v} {k}" for k, v in sorted(plan["roster_shape"].items()))
    lo, hi = plan["spread"]
    st.append(Paragraph(
        f"Built by playing <b>{plan['n_boards']} full drafts</b> from this seat, with the "
        f"other nine teams drawn from real ADP behaviour. Shown below is the median "
        f"outcome — its finished lineup projects to <b>{plan['projected_points']:.0f} points</b> "
        f"(across all boards: {lo:.0f}–{hi:.0f}). Resulting roster: {shape}.",
        S["body"]))
    st.append(Spacer(1, 4))
    st.append(Paragraph(
        "<i>Availability</i> is how often that player was still on the board at that pick "
        "across all simulated drafts. Anything under ~40% is a target you should expect to "
        "miss regularly — plan on the backup.", S["small"]))

    st.append(Paragraph("Round by round", S["h2"]))
    for r in plan["rounds"]:
        p = r["primary"]
        pl = p["player"]
        rows = [[Paragraph(f'<b>R{r["round"]}</b><br/><font size="7" color="#5c6673">'
                           f'pick {r["pick"]}</font>', S["cell"]),
                 Paragraph(f'{_pos_chip(pl.pos)} &nbsp;<b>{pl.name}</b>'
                           f'<br/><font size="7.5" color="#5c6673">{pl.team} · '
                           f'{pl.proj:.0f} proj · {pl.vor:.0f} VOR'
                           + (f' · bye {pl.bye}' if pl.bye else '') + '</font>', S["cell"]),
                 Paragraph(f'<b>{p["seen_pct"]}%</b>', S["cell"]),
                 Paragraph(p["why"], S["cellдим"])]]
        for b in r["backups"]:
            bp = b["player"]
            rows.append([Paragraph('<font size="7" color="#5c6673">backup</font>', S["cellдим"]),
                         Paragraph(f'{_pos_chip(bp.pos)} &nbsp;{bp.name}'
                                   f'<br/><font size="7.5" color="#5c6673">{bp.team} · '
                                   f'{bp.proj:.0f} proj</font>', S["cellдим"]),
                         Paragraph(f'{b["seen_pct"]}%', S["cellдим"]),
                         Paragraph(b["why"], S["cellдим"])])
        t = Table(rows, colWidths=[0.62 * inch, 2.25 * inch, 0.6 * inch, 3.68 * inch])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("LINEBELOW", (0, -1), (-1, -1), 0.6, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ]))
        st.append(KeepTogether([t, Spacer(1, 5)]))

    st.append(PageBreak())
    st.append(Paragraph("Where that leaves you", S["h2"]))
    lin = [[Paragraph("<b>Slot</b>", S["cell"]), Paragraph("<b>Player</b>", S["cell"]),
            Paragraph("<b>Proj</b>", S["cell"])]]
    for nm, pos, pr in plan["projected_lineup"]:
        lin.append([Paragraph(_pos_chip(pos), S["cell"]), Paragraph(nm, S["cell"]),
                    Paragraph(f"{pr:.0f}", S["cell"])])
    lin.append([Paragraph("", S["cell"]), Paragraph("<b>Starting lineup</b>", S["cell"]),
                Paragraph(f"<b>{plan['projected_points']:.0f}</b>", S["cell"])])
    lt = Table(lin, colWidths=[0.8 * inch, 3.0 * inch, 0.8 * inch])
    lt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, RULE),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    st.append(lt)

    st.append(Paragraph("How to read this", S["h2"]))
    st.append(Paragraph(
        "This is a plan, not a script. The value of it is the <b>shape</b> — which positions "
        "to attack in which rounds, and which names are realistic when your turn comes. "
        "The exact players will differ on the night; that is what the backups are for, and "
        "why each one carries its own reason. Take the live agent's call over this sheet "
        "whenever they disagree: it can see the actual board and this cannot.", S["body"]))
    st.append(Spacer(1, 8))
    st.append(Paragraph(
        "Sources — projections: ESPN Fantasy API and Sleeper (Rotowire), rescored under this "
        "league's exact rules. ADP and opponent behaviour: ESPN and Fantasy Football "
        "Calculator (which supplies the per-player standard deviation). Prior-year "
        "production, injury history and snap share: nflverse. Value over replacement, tiers "
        "and all simulation output: computed by this app.", S["small"]))

    doc.build(st)
    return buf.getvalue()
