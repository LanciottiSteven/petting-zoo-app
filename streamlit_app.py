"""
The Petting Zoo — draft assistant (Streamlit build, for easy hosting).

Same engine as the local FastAPI app, and the same visual design: read-only
panels are rendered as HTML through `pettingzoo.ui` so both apps match, while
anything you interact with stays a real Streamlit widget.
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
from pettingzoo.draft import simulate, optimal_lineup
from pettingzoo.advisor import advise
from pettingzoo.strategies import STRATEGIES, backtest, backtest_seasons, round_plan
from pettingzoo.season import build_league, simulate_season
from pettingzoo.league import (LEAGUE_NAME, MY_TEAM_NAME, N_TEAMS, ROSTER_SIZE,
                               DRAFT_DATE, snake_pick_numbers)

ASSETS = Path(__file__).resolve().parent / "pettingzoo" / "web" / "assets"
LOGO = ASSETS / "logo.png"
LOGO_SM = ASSETS / "logo-sm.png"

st.set_page_config(page_title="The Petting Zoo — Draft Assistant",
                   page_icon=str(ASSETS / "favicon.png") if
                   (ASSETS / "favicon.png").exists() else "🦁",
                   layout="wide")


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()
st.markdown(ui.CSS, unsafe_allow_html=True)
H = lambda s: st.markdown(s, unsafe_allow_html=True)


# ttl matters here: without it this cache would hold the very first pool for the
# life of the process and the per-source TTLs in sources.py would never be
# consulted. A rebuild only reads local files, so 30 min is cheap.
@st.cache_resource(ttl=1800, show_spinner="Loading player data…")
def load():
    poolmod.GAMES_MISSED_OVERRIDES = {k: (v[0], v[1])
                                      for k, v in store.get_overrides().items()}
    pool = build_pool()          # re-pulls only sources past their own TTL
    repl = compute_valuation(pool)
    return pool, repl, waiver_levels(pool)


ss = st.session_state
ss.setdefault("taken", [])
ss.setdefault("mine", [])
ss.setdefault("slot", store.get_setting("my_draft_slot") or 1)

pool, repl, waiver = load()
by_name = {p.name: p for p in pool}
ranked = sorted([p for p in pool if p.proj > 0], key=lambda p: -p.vor)
names = [p.name for p in ranked]

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    if LOGO_SM.exists():
        H(f'<div class="pz-brand"><img src="data:image/png;base64,{_b64(LOGO_SM)}" '
          f'alt="The Petting Zoo"></div>')
    else:
        H(f'<div style="font-size:17px;font-weight:650">🦁 {LEAGUE_NAME}</div>')
    H(f'<div class="dim" style="font-size:11.5px;text-align:center">'
      f'{N_TEAMS} teams · full PPR · {ROSTER_SIZE}-man roster</div>'
      f'<div class="dim" style="font-size:11.5px;margin-bottom:10px;text-align:center">'
      f'Draft {DRAFT_DATE[:10]} · you are <i>{MY_TEAM_NAME}</i></div>')

    ss.slot = st.selectbox("Your draft slot", range(1, N_TEAMS + 1),
                           index=int(ss.slot) - 1, key="w_slot")
    store.set_setting("my_draft_slot", ss.slot)
    picks = snake_pick_numbers(ss.slot)
    H(f'<div class="tag">picks {", ".join(map(str, picks[:7]))} …</div>')

    st.divider()
    if st.button("↻ Refresh all data", use_container_width=True, key="w_refresh"):
        with st.spinner("Re-pulling every source…"):
            errs = {}
            for nm, fn in sources.REFRESHERS.items():
                try:
                    fn(force=True)
                except Exception as e:
                    errs[nm] = str(e)
        load.clear()
        st.success("Refreshed.") if not errs else st.warning(f"Some failed: {errs}")
        st.rerun()

    status = sources.data_status()
    stale = [d for d in status if d["stale"]]
    with st.expander(f"Data freshness — "
                     f"{'all fresh' if not stale else f'{len(stale)} refreshing'}"):
        for d in status:
            nm = d["source"].replace(".json", "").replace(".csv", "")
            if d["missing"]:
                H(f'<div class="tag">• <b>{nm}</b> — not downloaded</div>')
            else:
                mins = d["age_seconds"] // 60
                ago = f"{mins}m ago" if mins < 90 else f"{mins // 60}h ago"
                H(f'<div class="tag">{"⟳" if d["stale"] else "✓"} <b>{nm}</b> — {ago} '
                  f'<span class="dim">(ttl {d["ttl_seconds"] // 3600}h)</span></div>')
        H('<div class="pz-hint">Sources re-pull automatically once past their limit. '
          'Refresh forces everything now.</div>')

    st.divider()
    pick_no = len(ss.taken) + 1
    H(ui.stats([("On the clock", pick_no, f"round {(pick_no - 1) // N_TEAMS + 1}", False),
                ("My roster", f"{len(ss.mine)}/{ROSTER_SIZE}", "", False)]))
    last = ss.taken[-1] if ss.taken else None
    if st.button(f"↶ Undo{f': {last}' if last else ''}", key="w_undo",
                 use_container_width=True, disabled=not ss.taken):
        gone = ss.taken.pop()
        if gone in ss.mine:
            ss.mine.remove(gone)
        st.rerun()
    if st.button("Clear draft", use_container_width=True, key="w_clear",
                 disabled=not ss.taken):
        ss.taken, ss.mine = [], []
        st.rerun()

tabs = st.tabs(["🎯 Draft Board", "📋 Strategies", "👥 Players",
                "🚑 Injuries", "🎲 Draft Sim", "🏆 Season Sim"])

# ------------------------------------------------------------------ board
with tabs[0]:
    taken_set = set(ss.taken)
    pick_no = len(ss.taken) + 1
    rnd = (pick_no - 1) // N_TEAMS + 1
    in_rd = (pick_no - 1) % N_TEAMS + 1
    on_clock = in_rd if rnd % 2 else N_TEAMS - in_rd + 1
    nxt = next((p for p in picks if p >= pick_no), None)
    mine_p = [by_name[n] for n in ss.mine if n in by_name]

    counts = {}
    for p in mine_p:
        counts[p.pos] = counts.get(p.pos, 0) + 1
    need = [f"{n - counts.get(k, 0)} {k}"
            for k, n in (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1),
                         ("K", 1), ("D/ST", 1)) if counts.get(k, 0) < n]

    H(ui.stats([
        ("On the clock", pick_no,
         f"round {rnd}, pick {in_rd}" +
         (" — YOU ARE UP" if on_clock == ss.slot else f" — slot {on_clock}"),
         on_clock == ss.slot),
        ("My next pick", nxt or "—",
         "then " + ", ".join(str(p) for p in picks if p > (nxt or 0))[:24] or "—", False),
        ("Roster filled", f"{len(ss.mine)}/{ROSTER_SIZE}",
         ("still need " + ", ".join(need)) if need else "starters complete", False),
    ]))

    c1, c2, c3 = st.columns([2, 1.4, 1.4])
    dyn = c1.toggle("Dynamic advisor", value=True, key="w_dyn",
                    help="Evaluates your roster, the remaining board, positional "
                         "runs and scarcity — not just raw value.")
    sims = c2.select_slider("Rollouts", [40, 70, 120, 200], value=120, key="w_sims")
    go = c3.button("▶ What should I take?", type="primary",
                   use_container_width=True, key="w_advise")
    if go:
        with st.spinner("Playing the rest of the draft out…"):
            ss.advice = advise(pool, ss.slot, ss.taken, ss.mine, repl, waiver,
                               pick_no=pick_no, n_sims=sims,
                               scan_sims=80 if dyn else 20, top_k=6)

    adv = ss.get("advice")
    if adv and adv["pick_no"] == pick_no:
        body = ""
        if dyn:
            why = "".join(f"<div>{ln.replace('**', '')}</div>" for ln in adv["reasoning"])
            body += f'<div class="pz-why">{why}</div>'
            rows = []
            for i, p in enumerate(adv["positions"]):
                capped = p["urgency"] < -900
                urg = ('<span class="dim">not needed</span>' if capped
                       else '<span class="good">best</span>' if p["urgency"] == 0
                       else f'<span class="bad">{p["urgency"]:.1f}</span>')
                sv = p["p_best_survives"]
                svs = ("—" if sv is None else
                       f'<span class="bad">never</span>' if sv == 0 else f"{sv * 100:.0f}%")
                rows.append(([
                    ui.td(ui.pill(p["pos"])),
                    ui.td(p["best_by_rollout"] or p["best_available"]),
                    ui.td(ui.num(p["rollout_mean"], 0), "num"),
                    ui.td(urg, "num"),
                    ui.td(f'<span class="{"bad" if p["cost_of_waiting"] >= 15 else ""}">'
                          f'{p["cost_of_waiting"]:.0f}</span>', "num"),
                    ui.td(p["count_above_replacement"], "num"),
                    ui.td(svs, "num"),
                    ui.td(f'<span class="dim">{p["tier_left"]}</span>', "num"),
                ], i == 0 and not capped))
            body += ui.table(
                [("Pos", 0), ("Best option", 0), ("Season pts", 1), ("vs best", 1),
                 ("Decay by next pick", 1), ("Left over repl.", 1),
                 ("Survives", 1), ("In tier", 1)], rows)
            hot = [k for k, v in (adv["runs"] or {}).items() if v.get("hot")]
            if hot:
                body += (f'<div class="pz-run"><b>Run alert:</b> {", ".join(hot)} '
                         f'going fast — the next tier there will clear quicker than ADP implies.</div>')

        crows = []
        for i, r in enumerate(adv["recommendations"]):
            cost = ('<span class="good">best</span>' if r["cost_vs_best"] == 0
                    else f'<span class="bad">{r["cost_vs_best"]:.1f}</span>')
            crows.append(([
                ui.td(f'<b>{r["name"]}</b>{ui.flag_html(r["flag"])}'),
                ui.td(ui.pill(r["pos"])),
                ui.td(ui.num(r["proj"]), "num"),
                ui.td(ui.num(r["vor"]), "num"),
                ui.td("—" if r["adp"] > 900 else ui.num(r["adp"]), "num"),
                ui.td(f'<b>{r["mean"]:.1f}</b>', "num"),
                ui.td(f'<span class="dim">{r["p10"]:.0f}–{r["p90"]:.0f}</span>', "num"),
                ui.td(cost, "num"),
            ], i == 0))
        body += '<h2 style="margin-top:16px">Candidates</h2>' + ui.table(
            [("Take", 0), ("Pos", 0), ("Proj", 1), ("VOR", 1), ("ADP", 1),
             ("Season pts", 1), ("Range", 1), ("Cost", 1)], crows)
        H(ui.card("Who should I take?", body,
                  "Each candidate is forced as your pick, then the rest of the draft is "
                  "played out many times. <b>Cost</b> is the projected lineup points you "
                  "give up versus the best option — it already accounts for who will still "
                  "be there at your next turn."))
    elif adv:
        st.info("Board has moved since that run — click again for a fresh read.")

    left, right = st.columns([3, 1.15])
    with left:
        H('<div class="pz-card" style="padding-bottom:6px"><h2>Board — tick a player as they go</h2></div>')
        f1, f2 = st.columns(2)
        q = f1.text_input("Search", placeholder="Type a name…", key="w_board_q",
                          label_visibility="collapsed")
        posf = f2.multiselect("Position", ["QB", "RB", "WR", "TE", "K", "D/ST"],
                              placeholder="All positions", key="w_board_pos",
                              label_visibility="collapsed")
        qn = norm_name(q) if q else ""
        avail = [p for p in ranked
                 if p.name not in taken_set
                 and (not posf or p.pos in posf)
                 and (not qn or qn in norm_name(p.name))][:60]

        board = pd.DataFrame([{
            "Taken": False, "Mine": False, "Player": p.name,
            "Pos": f"{p.pos}{p.pos_rank}", "Tier": p.tier, "Proj": p.proj,
            "VOR": p.vor, "ADP": None if p.adp > 900 else p.adp,
            "Bye": p.bye, "Flag": p.flag or "",
        } for p in avail])

        edited = st.data_editor(
            board, key=f"board_{len(ss.taken)}_{len(ss.mine)}",
            hide_index=True, use_container_width=True, height=440,
            column_config={
                "Taken": st.column_config.CheckboxColumn("Taken", width="small",
                                                         help="Drafted by another team"),
                "Mine": st.column_config.CheckboxColumn("Mine", width="small",
                                                        help="I drafted this player"),
                "Proj": st.column_config.NumberColumn(format="%.1f"),
                "VOR": st.column_config.NumberColumn(format="%.1f"),
                "ADP": st.column_config.NumberColumn(format="%.1f"),
            },
            disabled=["Player", "Pos", "Tier", "Proj", "VOR", "ADP", "Bye", "Flag"])

        if not edited.empty:
            new_mine = edited.loc[edited["Mine"], "Player"].tolist()
            new_taken = edited.loc[edited["Taken"] & ~edited["Mine"], "Player"].tolist()
            if new_mine or new_taken:
                for nm in new_mine:
                    if nm not in ss.taken:
                        ss.taken.append(nm)
                    if nm not in ss.mine:
                        ss.mine.append(nm)
                for nm in new_taken:
                    if nm not in ss.taken:
                        ss.taken.append(nm)
                st.rerun()
        H('<div class="pz-hint"><b>Taken</b> = drafted by another team. '
          '<b>Mine</b> = you drafted them (counts as taken too). Undo is in the sidebar.</div>')

    with right:
        if mine_p:
            pts, starters = optimal_lineup(mine_p)
            used = {id(p) for p in starters}
            rows = [([ui.td(f'<span class="tag">{p.pos}</span>'), ui.td(p.name),
                      ui.td(f"{p.proj:.0f}", "num")], False) for p in starters]
            rows.append(([ui.td(""), ui.td("<b>Total</b>"),
                          ui.td(f"<b>{pts:.0f}</b>", "num")], False))
            body = ui.table([("Slot", 0), ("Player", 0), ("Proj", 1)], rows)
            bench = [p for p in mine_p if id(p) not in used]
            if bench:
                body += ('<h2 style="margin-top:14px">Bench</h2>' + ui.table(
                    [("Pos", 0), ("Player", 0), ("Proj", 1)],
                    [([ui.td(ui.pill(p.pos)), ui.td(p.name),
                       ui.td(f"{p.proj:.0f}", "num")], False) for p in bench]))
            H(ui.card("My team", body))
        else:
            H(ui.card("My team", '<div class="dim" style="padding:18px 0;text-align:center">'
                                 'No players yet.</div>'))

    with st.expander("Correct the draft log (bulk edit)"):
        st.caption("The board is the fast way in. Use these to fix an out-of-order "
                   "entry or paste a batch.")
        t = st.multiselect("Everyone drafted so far (in order)", names,
                           default=ss.taken, key="fix_taken")
        m = st.multiselect("Which of those are mine", t,
                           default=[x for x in ss.mine if x in t], key="fix_mine")
        if st.button("Apply corrections", key="w_apply"):
            ss.taken, ss.mine = list(t), list(m)
            st.rerun()

# ------------------------------------------------------------------ strategies
with tabs[1]:
    c1, c2 = st.columns([3, 1])
    mode = c1.radio("Score by", ["Title odds (plays real seasons — slower, truer)",
                                 "Projected lineup points (fast)"],
                    horizontal=False, label_visibility="collapsed", key="w_stmode")
    run = c2.button("▶ Backtest all strategies", type="primary",
                    use_container_width=True, key="w_stbtn")
    if run:
        with st.spinner("Drafting and simulating…"):
            if mode.startswith("Title"):
                ss.strat = ("season", backtest_seasons(pool, ss.slot, repl, waiver,
                                                       n_drafts=20, n_seasons=80))
            else:
                ss.strat = ("points", backtest(pool, ss.slot, repl, waiver, n_sims=120))

    if ss.get("strat"):
        kind, res = ss.strat
        if kind == "season":
            mx = max(r["title_odds"] for r in res)
            rows = [([
                ui.td(f'<b>{r["name"]}</b><div class="dim" style="font-size:11.5px">'
                      f'{r["blurb"]}</div>'),
                ui.td(f'<b>{r["title_odds"] * 100:.1f}%</b>', "num"),
                ui.td(f'{r["playoff_odds"] * 100:.0f}%', "num"),
                ui.td(f'{r["exp_wins"]:.2f}', "num"),
                ui.td(f'<span class="dim">{r["worst_draft_title"] * 100:.0f}–'
                      f'{r["best_draft_title"] * 100:.0f}%</span>', "num"),
                ui.td(ui.bar(r["title_odds"] / mx)),
            ], i == 0) for i, r in enumerate(res)]
            H(ui.card("Which strategy actually wins in this league?", ui.table(
                [("Strategy", 0), ("Title odds", 1), ("Playoffs", 1), ("Wins", 1),
                 ("Spread across drafts", 1), ("", 0)], rows),
                "Baseline for a 10-team league is 10%. The <b>spread</b> is the range "
                "across individual drafts within one strategy — it is wide, which is the "
                "real lesson: which players you land matters more than the plan's name."))
        else:
            rows = [([
                ui.td(f'<b>{r["name"]}</b><div class="dim" style="font-size:11.5px">'
                      f'{r["blurb"]}</div>'),
                ui.td(f'<b>{r["mean"]:.1f}</b>', "num"),
                ui.td(f'<span class="dim">{r["p10"]:.0f}–{r["p90"]:.0f}</span>', "num"),
                ui.td('<span class="good">best</span>' if r["vs_best"] == 0
                      else f'<span class="bad">{r["vs_best"]:.1f}</span>', "num"),
                ui.td(f'<span class="tag">{r["typical_open"]} '
                      f'<span class="dim">{r["open_pct"]}%</span></span>'),
            ], i == 0) for i, r in enumerate(res)]
            H(ui.card("Which strategy actually wins in this league?", ui.table(
                [("Strategy", 0), ("Mean lineup", 1), ("p10–p90", 1), ("vs best", 1),
                 ("Typical open", 0)], rows),
                "Ranking by projected points quietly favours the Balanced plan, because "
                "that is the number it maximises. <b>Title odds</b> is the honest test."))
    else:
        H(ui.card("Which strategy actually wins in this league?",
                  '<div class="dim" style="padding:18px 0;text-align:center">'
                  'Run a backtest to compare all eight plans.</div>',
                  "Each strategy is drafted against the ADP opponent model and scored."))

    pickname = st.selectbox("Round-by-round plan", [s.name for s in STRATEGIES],
                            key="w_stplan")
    strat = next(s for s in STRATEGIES if s.name == pickname)
    rows = [([ui.td(f'<span class="tag">R{r["round"]}</span>'),
              ui.td(f'<span class="tag">#{r["pick"]}</span>', "num"),
              ui.td(r["target"])], False) for r in round_plan(strat, ss.slot)]
    H(ui.card(f"{strat.name} — plan for slot {ss.slot}",
              f'<div class="pz-why"><div><b>{strat.blurb}</b></div>'
              f'<div class="dim">{strat.rationale}</div></div>' +
              ui.table([("Round", 0), ("Your pick", 1), ("Target", 0)], rows)))

# ------------------------------------------------------------------ players
with tabs[2]:
    c1, c2 = st.columns([1, 3])
    pf = c1.multiselect("Position", ["QB", "RB", "WR", "TE", "K", "D/ST"],
                        placeholder="All positions", key="w_players_pos",
                        label_visibility="collapsed")
    pq = c2.text_input("Search", placeholder="Search players…", key="w_players_q",
                       label_visibility="collapsed")
    pqn = norm_name(pq) if pq else ""
    rows = [([
        ui.td(p.name), ui.td(ui.pill(p.pos, str(p.pos_rank))),
        ui.td(p.tier, "num"), ui.td(f"{p.proj:.1f}", "num"),
        ui.td(f"{p.vor:.1f}", "num"),
        ui.td("—" if p.adp > 900 else f"{p.adp:.1f}", "num"),
        ui.td(f'<span class="{"bad" if p.proj_spread > 35 else ""}">'
              f'{p.proj_spread:.1f}</span>', "num"),
        ui.td(f"{p.week_sd:.1f}", "num"),
        ui.td(f'<span class="dim">{ui.num(p.actual_2025, 0)}</span>', "num"),
        ui.td(p.bye or "—", "num"),
        ui.td(ui.flag_html(p.flag, p.games_missed)),
    ], False) for p in ranked
        if (not pf or p.pos in pf) and (not pqn or pqn in norm_name(p.name))][:400]
    H(ui.card("Player pool — ranked by value over replacement", ui.table(
        [("Player", 0), ("Pos", 0), ("Tier", 1), ("Proj", 1), ("VOR", 1), ("ADP", 1),
         ("Disagree", 1), ("SD/wk", 1), ("2025", 1), ("Bye", 1), ("Flag", 0)],
        rows, scroll=True),
        "<b>Disagree</b> is the gap between ESPN's and Sleeper's projections — a large "
        "value means the sources genuinely disagree and the player is riskier than one "
        "number suggests. <b>SD/wk</b> is measured from real 2022–2025 game logs."))

# ------------------------------------------------------------------ injuries
with tabs[3]:
    sev = {"SUSPENDED": 0, "IR": 1, "OUT": 1, "PUP": 2, "NFI": 2,
           "Doubtful": 3, "Questionable": 4}
    flagged = sorted([p for p in pool if (p.flag or p.games_missed) and p.adp < 900],
                     key=lambda p: (0 if p.games_missed else 1, sev.get(p.flag, 5), p.adp))
    rows = [([
        ui.td(p.name), ui.td(ui.pill(p.pos)),
        ui.td(f"{p.adp:.1f}", "num"),
        ui.td(ui.flag_html(p.flag, p.games_missed) or '<span class="dim">—</span>'),
        ui.td(p.games_missed or "", "num"),
        ui.td(f'<span class="dim">{p.note or ""}</span>'),
    ], bool(p.games_missed)) for p in flagged]
    H(ui.card("Flagged players", ui.table(
        [("Player", 0), ("Pos", 0), ("ADP", 1), ("Status", 0),
         ("Games out", 1), ("Note", 0)], rows, scroll=True),
        "No public feed reports <b>how many</b> games a suspension or injury costs — "
        "only a status flag. Set it below and the projection is discounted pro-rata "
        "everywhere in the app."))
    with st.form("override"):
        c1, c2, c3, c4 = st.columns([3, 1, 3, 1])
        nm = c1.selectbox("Player", names, key="w_ov_name")
        gm = c2.number_input("Games out", 0, 17, 0, key="w_ov_games")
        note = c3.text_input("Reason", placeholder="e.g. 2-game suspension",
                             key="w_ov_note")
        c4.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if c4.form_submit_button("Save", type="primary", use_container_width=True):
            store.set_override(nm, int(gm), note) if gm > 0 else store.clear_override(nm)
            load.clear()
            st.rerun()

# ------------------------------------------------------------------ draft sim
with tabs[4]:
    c1, c2 = st.columns([3, 1])
    n = c1.select_slider("Drafts to simulate", [50, 100, 250, 500], value=250,
                         key="w_dsim_n")
    if c2.button("▶ Run draft simulation", type="primary",
                 use_container_width=True, key="w_dsim"):
        with st.spinner("Drafting…"):
            r = simulate(pool, ss.slot, n_sims=n, repl_pts=repl, waiver=waiver)
            store.save_run("draft", {"my_slot": ss.slot, "n_sims": n}, r)
            ss.dsim = r
    if ss.get("dsim"):
        r = ss.dsim
        H(ui.stats([("Mean starting lineup", r["mean_starting_points"], "", True),
                    ("10th–90th percentile", f'{r["p10"]}–{r["p90"]}', "", False),
                    ("Drafts simulated", r["n_sims"], "", False)]))
        rows = [([ui.td(f'<span class="tag">#{pk}</span>'),
                  ui.td(" · ".join(f'{nm} <span class="dim">'
                                   f'{round(100 * ct / r["n_sims"])}%</span>'
                                   for nm, ct in opts[:4]))], False)
                for pk, opts in r["pick_frequency"].items()]
        H(ui.card("Who you end up with, by pick",
                  ui.table([("Pick", 0), ("Most likely selections", 0)], rows)))

# ------------------------------------------------------------------ season sim
with tabs[5]:
    c1, c2, c3 = st.columns([2, 1.4, 1.2])
    n = c1.select_slider("Seasons to simulate", [200, 500, 1000], value=500,
                         key="w_ssim_n")
    use_mine = c2.checkbox("Use my drafted roster", value=bool(ss.mine), key="w_ssim_mine")
    if c3.button("▶ Run season simulation", type="primary",
                 use_container_width=True, key="w_ssim"):
        with st.spinner("Playing seasons…"):
            mp = [by_name[x] for x in ss.mine if x in by_name] if use_mine else None
            rosters = build_league(pool, ss.slot, my_players=mp,
                                   repl_pts=repl, waiver=waiver)
            r = simulate_season(rosters, ss.slot, n_sims=n)
            store.save_run("season", {"my_slot": ss.slot, "n_sims": n}, r)
            ss.ssim = r
    if ss.get("ssim"):
        r = ss.ssim
        me = next(t for t in r["teams"] if t["is_me"])
        H(ui.stats([
            ("My title odds", f'{me["title_odds"] * 100:.1f}%', "baseline 10%", True),
            ("My playoff odds", f'{me["playoff_odds"] * 100:.1f}%',
             f'expected record {me["exp_wins"]}–{14 - me["exp_wins"]:.1f}', False),
            ("My weekly score", r["my_weekly"]["mean"],
             f'{r["my_weekly"]["p10"]}–{r["my_weekly"]["p90"]} typical', False)]))
        mx = max(t["title_odds"] for t in r["teams"]) or 1
        rows = [([
            ui.td(f'<b>{t["team"]}</b>' if t["is_me"] else t["team"]),
            ui.td(f'<span class="dim">{t["division"] or ""}</span>'),
            ui.td(f'{t["exp_wins"]:.2f}', "num"), ui.td(f'{t["exp_points"]:.0f}', "num"),
            ui.td(f'{t["playoff_odds"] * 100:.0f}%', "num"),
            ui.td(f'<b>{t["title_odds"] * 100:.1f}%</b>', "num"),
            ui.td(ui.bar(t["title_odds"] / mx)),
        ], t["is_me"]) for t in r["teams"]]
        H(ui.card("Final standings odds", ui.table(
            [("Team", 0), ("Div", 0), ("Wins", 1), ("Points", 1),
             ("Playoffs", 1), ("Title", 1), ("", 0)], rows),
            "The other nine teams are drafted by the ADP model, so these odds measure "
            "your roster against <i>simulated</i> opponents. Directional, not literal."))
