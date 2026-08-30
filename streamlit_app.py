"""
The Petting Zoo — live draft room.

One screen, no tabs. Everything the draft needs is visible at once: the board,
your roster and its gaps, what the other nine teams have taken, the agent's
call, and the research behind it.
"""
from __future__ import annotations
import base64
from pathlib import Path

import pandas as pd
import streamlit as st

from pettingzoo import sources, store, ui
from pettingzoo import pool as poolmod
from pettingzoo.pool import build_pool, norm_name
from pettingzoo.valuation import compute_valuation, waiver_levels
from pettingzoo.draft import optimal_lineup
from pettingzoo.draftroom import LiveDraft, agent, STARTER_SLOTS
from pettingzoo.research import dossier, summary_lines
from pettingzoo.league import (LEAGUE_NAME, MY_TEAM_NAME, N_TEAMS, ROSTER_SIZE,
                               DRAFT_DATE, TEAM_NAMES)

ASSETS = Path(__file__).resolve().parent / "pettingzoo" / "web" / "assets"
st.set_page_config(page_title="The Petting Zoo — Draft Room",
                   page_icon=str(ASSETS / "favicon.png") if (ASSETS / "favicon.png").exists() else "🦁",
                   layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)
H = lambda s: st.markdown(s, unsafe_allow_html=True)
b64 = lambda p: base64.b64encode(Path(p).read_bytes()).decode()


@st.cache_resource(ttl=1800, show_spinner="Loading players…")
def load():
    poolmod.GAMES_MISSED_OVERRIDES = {k: (v[0], v[1]) for k, v in store.get_overrides().items()}
    p = build_pool()
    return p, compute_valuation(p), waiver_levels(p)


pool, repl, waiver = load()
by_name = {norm_name(p.name): p for p in pool}
ranked = sorted([p for p in pool if p.proj > 0], key=lambda p: -p.vor)

ss = st.session_state
if "live" not in ss:
    ss.live = LiveDraft(my_slot=int(store.get_setting("my_draft_slot") or 1))
live: LiveDraft = ss.live
ss.setdefault("selected", None)
ss.setdefault("agent_cache", {})


# ------------------------------------------------------------------ actions
def _resolve(row_idx: int) -> str | None:
    view = ss.get("board_view") or []
    return view[row_idx] if 0 <= row_idx < len(view) else None


def _take(row_idx: int, slot: int | None):
    nm = _resolve(row_idx)
    if nm and norm_name(nm) not in {norm_name(x) for x in live.taken_names()}:
        live.add(nm, slot)
        ss.selected = None


def on_mine():
    c = ss.get("click_mine")
    if c is not None:
        _take(c["row"], live.my_slot)


def on_info():
    c = ss.get("click_info")
    if c is not None:
        ss.selected = _resolve(c["row"])


def on_taken():
    c = ss.get("click_taken")
    if c is not None:
        # in a live draft picks arrive in order, so the seat on the clock is
        # nearly always right; the override below fixes the exceptions
        slot = ss.get("assign_to_slot") or live.on_the_clock
        if slot == live.my_slot:
            slot = next((s for s in range(1, N_TEAMS + 1) if s != live.my_slot), 1)
        _take(c["row"], slot)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    if (ASSETS / "logo-sm.png").exists():
        H(f'<div class="pz-brand"><img src="data:image/png;base64,{b64(ASSETS/"logo-sm.png")}"></div>')
    H(f'<div class="dim" style="text-align:center;font-size:11.5px">{N_TEAMS} teams · full PPR · '
      f'{ROSTER_SIZE} roster<br>you are <i>{MY_TEAM_NAME}</i></div>')
    st.divider()

    if not live.started:
        slot = st.selectbox("Your draft slot", range(1, N_TEAMS + 1),
                            index=live.my_slot - 1, key="w_slot")
        live.my_slot = slot
        store.set_setting("my_draft_slot", slot)
        H(f'<div class="tag">picks {", ".join(map(str, live.my_picks[:7]))} …</div>')
        with st.expander("Name the other seats (optional)"):
            st.caption("Only matters for reading the league table; seat order is "
                       "set by your LM and is not published anywhere.")
            others = [t for t in TEAM_NAMES if t != MY_TEAM_NAME]
            for s in range(1, N_TEAMS + 1):
                if s == live.my_slot:
                    continue
                live.seat_names[s] = st.text_input(
                    f"Seat {s}", value=live.seat_names.get(s, ""),
                    placeholder=others[(s - 1) % len(others)], key=f"seat_{s}")
    else:
        H(f'<div class="tag">your slot {live.my_slot} · picks '
          f'{", ".join(str(p) for p in live.my_picks if p >= live.pick_no)[:34]}…</div>')
        st.divider()
        last = live.picks[-1].player if live.picks else None
        if st.button(f"↶ Undo{f': {last}' if last else ''}", use_container_width=True,
                     disabled=not live.picks, key="w_undo"):
            live.undo(); ss.selected = None; st.rerun()
        if st.button("Reset draft", use_container_width=True, key="w_reset"):
            ss.live = LiveDraft(my_slot=live.my_slot); ss.selected = None
            ss.agent_cache = {}; st.rerun()

    st.divider()
    if st.button("↻ Refresh player data", use_container_width=True, key="w_refresh"):
        with st.spinner("Re-pulling sources…"):
            for fn in sources.REFRESHERS.values():
                try: fn(force=True)
                except Exception: pass
        load.clear(); st.rerun()
    stale = [d for d in sources.data_status() if d["stale"]]
    H(f'<div class="tag">{"all sources fresh" if not stale else f"{len(stale)} refreshing next load"}</div>')


# ------------------------------------------------------------------ pre-draft
if not live.started:
    if (ASSETS / "logo.png").exists():
        st.markdown(
            f'<div style="text-align:center;margin:8px 0 4px">'
            f'<img src="data:image/png;base64,{b64(ASSETS/"logo.png")}" style="max-width:290px">'
            f'</div>', unsafe_allow_html=True)
    H(f'<div style="text-align:center" class="dim">Draft {DRAFT_DATE[:10]} · '
      f'you pick from seat <b>{live.my_slot}</b> · {len(ranked)} players loaded</div>')

    c1, c2, c3 = st.columns([1, 1.1, 1])
    with c2:
        st.write("")
        if st.button("▶  INITIATE DRAFT", type="primary", use_container_width=True,
                     key="w_start"):
            live.started = True
            st.rerun()

    top = ranked[:12]
    rows = [([ui.td(f"{i}"), ui.td(f"<b>{p.name}</b>{ui.flag_html(p.flag, p.games_missed)}"),
              ui.td(ui.pill(p.pos, str(p.pos_rank))), ui.td(f"{p.proj:.0f}", "num"),
              ui.td(f"{p.vor:.0f}", "num"),
              ui.td("—" if p.adp > 900 else f"{p.adp:.1f}", "num")], False)
             for i, p in enumerate(top, 1)]
    H(ui.card("Board preview — best available by value over replacement",
              ui.table([("#", 0), ("Player", 0), ("Pos", 0), ("Proj", 1), ("VOR", 1), ("ADP", 1)], rows),
              "Set your slot on the left, then hit Initiate Draft. The agent starts "
              "advising from your first pick."))
    st.stop()


# ------------------------------------------------------------------ live
taken_norm = {norm_name(n) for n in live.taken_names()}
my_players = [by_name[norm_name(n)] for n in live.my_roster() if norm_name(n) in by_name]
mine_up = live.on_the_clock == live.my_slot

H(ui.stats([
    ("Pick", live.pick_no, f"round {live.round_no}", mine_up),
    ("On the clock", "YOU" if mine_up else live.name_for(live.on_the_clock),
     "your pick — go" if mine_up else f"seat {live.on_the_clock}", mine_up),
    ("Your next pick", live.next_pick or "—",
     f"{live.picks_until_my_turn} picks away" if not mine_up else "now", False),
    ("Roster", f"{len(my_players)}/{ROSTER_SIZE}",
     ", ".join(f"need {n} {p}" for p, n in live.needs_of(live.my_slot, by_name).items()) or "starters set",
     False),
]))

# ---- the agent -----------------------------------------------------------
ck = (live.pick_no, len(live.picks))
run_agent = st.button("🧠  What should I take?", type="primary", key="w_agent") or \
            (mine_up and ck in ss.agent_cache)
if run_agent and ck not in ss.agent_cache:
    with st.spinner("Reading the board and playing the draft forward…"):
        ss.agent_cache = {ck: agent(pool, live, repl, waiver)}
adv = ss.agent_cache.get(ck)

if adv:
    body = '<div class="pz-why">' + "".join(
        f"<div>{ui.md(l)}</div>" for l in adv["rationale"]) + "</div>"
    focus = [p for p in adv["positions"] if p["urgency"] is not None][:4]
    frows = []
    for i, p in enumerate(focus):
        frows.append(([
            ui.td(ui.pill(p["pos"])),
            ui.td(f"<b>{p['best_by_rollout']}</b>"),
            ui.td(f"{p['rollout_mean']:.0f}" if p["rollout_mean"] else "—", "num"),
            ui.td('<span class="good">best</span>' if p["urgency"] == 0
                  else f'<span class="bad">{p["urgency"]:.1f}</span>', "num"),
            ui.td(f'<span class="{"bad" if p["decay"]>=12 else ""}">{p["decay"]:.0f}</span>', "num"),
            ui.td(f'<b>{p["teams_needing"]}</b>' if p["teams_needing"] >= 3
                  else str(p["teams_needing"]), "num"),
            ui.td(str(p["left_above_repl"]), "num"),
            ui.td("—" if p["survives"] is None else
                  ('<span class="bad">never</span>' if p["survives"] == 0
                   else f'{p["survives"]*100:.0f}%'), "num"),
        ], i == 0))
    body += '<div class="pz-h">Where to focus</div>' + ui.table(
        [("Pos", 0), ("Best available", 0), ("Season pts", 1), ("vs best", 1),
         ("Falls by", 1), ("Slots needed", 1), ("Left over repl.", 1),
         ("Survives", 1)], frows)
    if adv["alternatives"]:
        arows = [([ui.td(f"<b>{a['name']}</b>{ui.flag_html(a.get('flag'))}"),
                   ui.td(ui.pill(a["pos"])),
                   ui.td(f"{a['proj']:.0f}", "num"),
                   ui.td(f"{a['cost_vs_best']:.1f}", "num"),
                   ui.td(f'<span class="dim">{a["why"]}</span>', "wrap")], False)
                  for a in adv["alternatives"][:5]]
        body += '<div class="pz-h">Also consider</div>' + ui.table(
            [("Player", 0), ("Pos", 0), ("Proj", 1), ("Cost", 1), ("Why it is on the list", 0)], arows)
    H(ui.card("Draft agent", body,
              "<b>Slots needed</b> is how many starter slots at that position are still "
              "empty across the teams picking before your next turn — that is what "
              "actually causes a run. <b>Falls by</b> is how far the best player there "
              "drops between now and then. <b>Survives</b> is how often the top name is "
              "still on the board when you pick again."))

# ---- board | my team | league -------------------------------------------
left, right = st.columns([2.15, 1])

with left:
    f1, f2, f3 = st.columns([2, 2, 1.4])
    q = f1.text_input("Search", placeholder="Search player…", label_visibility="collapsed",
                      key="w_q")
    posf = f2.multiselect("Position", ["QB", "RB", "WR", "TE", "K", "D/ST"],
                          placeholder="All positions", label_visibility="collapsed", key="w_pos")
    assign = f3.selectbox("Assign 'Taken' to",
                          ["On the clock"] + [f"Seat {s} — {live.name_for(s)}"
                                              for s in range(1, N_TEAMS + 1) if s != live.my_slot],
                          label_visibility="collapsed", key="w_assign")
    ss.assign_to_slot = None if assign == "On the clock" else int(assign.split()[1])

    qn = norm_name(q) if q else ""
    avail = [p for p in ranked
             if norm_name(p.name) not in taken_norm
             and (not posf or p.pos in posf)
             and (not qn or qn in norm_name(p.name))][:70]
    ss.board_view = [p.name for p in avail]

    df = pd.DataFrame([{
        "Mine": "＋ Mine", "Taken": "✕ Taken", "Info": "🔍",
        "Player": p.name, "Pos": f"{p.pos}{p.pos_rank}", "Tier": p.tier,
        "Proj": p.proj, "VOR": p.vor,
        "ADP": None if p.adp > 900 else p.adp, "Bye": p.bye,
        "2025": p.actual_2025, "Flag": p.flag or "",
    } for p in avail])

    sel = st.dataframe(
        df, hide_index=True, use_container_width=True, height=430,
        on_select="rerun", selection_mode="single-row", key="w_board",
        column_config={
            "Mine": st.column_config.ButtonColumn(
                "", width="small", type="primary", on_click=on_mine, key="click_mine",
                help="I drafted this player"),
            "Taken": st.column_config.ButtonColumn(
                "", width="small", on_click=on_taken, key="click_taken",
                help="Another team drafted this player"),
            "Info": st.column_config.ButtonColumn(
                "", width="small", type="tertiary", on_click=on_info, key="click_info",
                help="Show research, 2025 game log and sources"),
            "Player": st.column_config.TextColumn(width="medium"),
            "Pos": st.column_config.TextColumn(width="small"),
            "Tier": st.column_config.NumberColumn(width="small"),
            "Proj": st.column_config.NumberColumn(format="%.1f", width="small"),
            "VOR": st.column_config.NumberColumn(format="%.1f", width="small"),
            "ADP": st.column_config.NumberColumn(format="%.1f", width="small"),
            "Bye": st.column_config.NumberColumn(width="small"),
            "2025": st.column_config.NumberColumn(format="%.0f", width="small"),
            "Flag": st.column_config.TextColumn(width="small"),
        })
    rows_sel = (sel.selection.rows if hasattr(sel, "selection") else []) or []
    if rows_sel and not ss.get("selected"):
        ss.selected = ss.board_view[rows_sel[0]] if rows_sel[0] < len(ss.board_view) else None
    H('<div class="pz-hint"><b>＋ Mine</b> adds to your roster. <b>✕ Taken</b> assigns the '
      'player to whoever is on the clock — use the dropdown above to attribute it to a '
      'different seat. <b>🔍</b> opens that player\'s research, 2025 game log and sources '
      'below the board.</div>')

with right:
    if my_players:
        pts, starters = optimal_lineup(my_players)
        used = {id(p) for p in starters}
        rows = [([ui.td(f'<span class="tag">{p.pos}</span>'), ui.td(p.name),
                  ui.td(f"{p.proj:.0f}", "num")], False) for p in starters]
        rows.append(([ui.td(""), ui.td("<b>Total</b>"), ui.td(f"<b>{pts:.0f}</b>", "num")], False))
        body = ui.table([("Slot", 0), ("Player", 0), ("Proj", 1)], rows)
        bench = [p for p in my_players if id(p) not in used]
        if bench:
            body += '<div class="pz-h">Bench</div>' + ui.table(
                [("Pos", 0), ("Player", 0), ("Proj", 1)],
                [([ui.td(ui.pill(p.pos)), ui.td(p.name), ui.td(f"{p.proj:.0f}", "num")], False)
                 for p in bench])
    else:
        body = '<div class="dim" style="padding:14px 0;text-align:center">No picks yet.</div>'
    gaps = live.needs_of(live.my_slot, by_name)
    if gaps:
        body += ('<div class="pz-h">Still to fill</div>' +
                 " ".join(f'<span class="pill {ui.POS_CLS.get(p,p)}">{p}'
                          f'{" ×"+str(n) if n>1 else ""}</span>' for p, n in gaps.items()))
    H(ui.card("My team", body))

    lrows = []
    for t in live.league_table(by_name):
        shape = " ".join(f"{v}{k}" for k, v in sorted(t["counts"].items())) or "—"
        need = ", ".join(f"{k}" for k in t["needs"] if k != "FLEX") or "set"
        lrows.append(([ui.td(f'<b>{t["team"]}</b>' if t["is_me"] else t["team"]),
                       ui.td(str(t["n"]), "num"),
                       ui.td(f'<span class="tag">{shape}</span>'),
                       ui.td(f'<span class="dim">{need}</span>')], t["is_me"]))
    H(ui.card("League", ui.table(
        [("Team", 0), ("N", 1), ("Roster shape", 0), ("Needs", 0)], lrows),
        "Who still needs what drives the agent's <b>Teams needing</b> column."))

# ---- research ------------------------------------------------------------
if ss.selected:
    p = by_name.get(norm_name(ss.selected))
    if p:
        d = dossier(p, repl)
        head = (f'<div class="pz-why"><div style="font-size:16px;margin-bottom:6px">'
                f'<b>{p.name}</b> &nbsp;{ui.pill(p.pos, str(p.pos_rank))} '
                f'<span class="dim">{p.team} · bye {p.bye or "—"}</span>'
                f'{ui.flag_html(p.flag, p.games_missed)}</div>')
        for s in summary_lines(p, repl):
            head += f"<div>{ui.md(s)}</div>"
        head += "</div>"

        blocks = []
        for title, rowset in d["blocks"]:
            # Two columns, with the source stacked under the value. A third
            # column for the source does not fit inside a grid track and gets
            # clipped off the right edge.
            rows = [([ui.td(lab, "wrap-label"),
                      ui.td(f'{val}<div class="tag" style="margin-top:2px">'
                            f'{d["sources"][src][0]}</div>', "num")], False)
                    for lab, val, src in rowset]
            blocks.append(f'<div class="pz-h" style="margin-top:0">{title}</div>'
                          + ui.table([("", 0), ("Value / source", 1)], rows))
        body = head + ui.cols(blocks, min_px=300)

        if d["game_log"]:
            g = d["game_log"]
            grows = [([ui.td(f'<span class="tag">W{x["week"]}</span>'),
                       ui.td(f'<span class="dim">{x["opp"] or ""}</span>'),
                       ui.td(f'{x["pts"]:.1f}', "num"),
                       ui.td(f'{x["targets"]:.0f}' if x["targets"] else "", "num"),
                       ui.td(f'{x["carries"]:.0f}' if x["carries"] else "", "num"),
                       ui.td(f'{x["rec_yds"]+x["rush_yds"]+x["pass_yds"]:.0f}', "num"),
                       ui.td(f'{x["tds"]:.0f}' if x["tds"] else "", "num")], x["pts"] >= 20)
                     for x in g]
            body += ('<div class="pz-h">2025 game by game '
                     '<span class="dim" style="text-transform:none;letter-spacing:0">'
                     '— highlighted rows are 20+ point weeks · source: nflverse game logs'
                     '</span></div>' + ui.table(
                [("Wk", 0), ("Opp", 0), ("Pts", 1), ("Tgt", 1), ("Car", 1), ("Yds", 1), ("TD", 1)],
                grows, scroll=True))

        H(ui.card(f"Research — {p.name}", body,
                  "Every row names the feed it came from. Nothing here is an opinion: "
                  "projections are vendor numbers rescored under your league's rules, and "
                  "the 2025 figures are measured from actual game logs."))

        a1, a2, a3 = st.columns([1, 1, 3])
        if a1.button(f"＋ Draft {p.name}", type="primary", use_container_width=True, key="w_take_me"):
            live.add(p.name, live.my_slot); ss.selected = None; st.rerun()
        if a2.button("✕ Taken by another", use_container_width=True, key="w_take_other"):
            slot = ss.get("assign_to_slot") or live.on_the_clock
            if slot == live.my_slot:
                slot = next((s for s in range(1, N_TEAMS + 1) if s != live.my_slot), 1)
            live.add(p.name, slot); ss.selected = None; st.rerun()
