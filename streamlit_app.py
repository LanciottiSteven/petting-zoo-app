"""
The Petting Zoo — draft assistant (Streamlit build, for easy hosting).

Same engine as the local FastAPI app; this is just a second presentation layer
over the `pettingzoo` package, so there is no duplicated draft logic.
"""
from __future__ import annotations
import time
import pandas as pd
import streamlit as st

from pettingzoo import sources, store
from pettingzoo import pool as poolmod
from pettingzoo.pool import build_pool, norm_name
from pettingzoo.valuation import compute_valuation, waiver_levels
from pettingzoo.draft import simulate, optimal_lineup
from pettingzoo.advisor import advise
from pettingzoo.strategies import STRATEGIES, backtest, backtest_seasons, round_plan
from pettingzoo.season import build_league, simulate_season
from pettingzoo.league import (LEAGUE_NAME, MY_TEAM_NAME, N_TEAMS, ROSTER_SIZE,
                               DRAFT_DATE, snake_pick_numbers)

st.set_page_config(page_title="The Petting Zoo — Draft Assistant",
                   page_icon="🦁", layout="wide")


# ttl matters here: without it this cache would hold the very first pool for
# the entire life of the process, and the per-source TTLs in sources.py would
# never get consulted. 30 minutes is cheap (a rebuild reads local files) and it
# is what lets a long-running hosted app pick up fresh ADP and injury news.
@st.cache_resource(ttl=1800, show_spinner="Loading player data…")
def load():
    poolmod.GAMES_MISSED_OVERRIDES = {k: (v[0], v[1])
                                      for k, v in store.get_overrides().items()}
    pool = build_pool()          # re-pulls only the sources past their own TTL
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

# ----------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🦁 The Petting Zoo")
    st.caption(f"{N_TEAMS} teams · full PPR · {ROSTER_SIZE}-man roster")
    st.caption(f"Draft {DRAFT_DATE[:10]} · you are *{MY_TEAM_NAME}*")
    ss.slot = st.selectbox("Your draft slot", range(1, N_TEAMS + 1),
                           index=int(ss.slot) - 1)
    store.set_setting("my_draft_slot", ss.slot)
    picks = snake_pick_numbers(ss.slot)
    st.caption("Your picks: " + ", ".join(map(str, picks[:7])) + " …")

    if st.button("↻ Refresh all data", use_container_width=True):
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
    label = ("all sources fresh" if not stale
             else f"{len(stale)} source(s) refreshing on next load")
    with st.expander(f"Data freshness — {label}"):
        for d in status:
            nm = d["source"].replace(".json", "").replace(".csv", "")
            if d["missing"]:
                st.caption(f"• **{nm}** — not downloaded yet")
            else:
                mins = d["age_seconds"] // 60
                ago = f"{mins} min ago" if mins < 90 else f"{mins//60} h ago"
                mark = "⟳" if d["stale"] else "✓"
                st.caption(f"{mark} **{nm}** — {ago} "
                           f"(refreshes after {d['ttl_seconds']//3600}h)")
        st.caption("Sources re-pull automatically once past their own limit. "
                   "Hit Refresh to force everything now.")

    st.divider()
    st.caption("**Draft state**")
    st.metric("On the clock", len(ss.taken) + 1)
    st.metric("My roster", f"{len(ss.mine)}/{ROSTER_SIZE}")
    if st.button("Clear draft", use_container_width=True):
        ss.taken, ss.mine = [], []
        st.rerun()

tabs = st.tabs(["🎯 Draft Board", "📋 Strategies", "👥 Players",
                "🚑 Injuries", "🎲 Draft Sim", "🏆 Season Sim"])

# ----------------------------------------------------------------- board
with tabs[0]:
    c1, c2 = st.columns(2)
    with c1:
        ss.taken = st.multiselect(
            "Everyone drafted so far (in order)", names, default=ss.taken,
            help="Type to search. Add every pick as it happens, including yours.")
    with c2:
        ss.mine = st.multiselect(
            "Which of those are mine", [n for n in ss.taken], default=
            [m for m in ss.mine if m in ss.taken])

    pick_no = len(ss.taken) + 1
    rnd = (pick_no - 1) // N_TEAMS + 1
    on_clock = (rnd % 2 and (pick_no - 1) % N_TEAMS + 1 or
                N_TEAMS - (pick_no - 1) % N_TEAMS)
    a, b, c = st.columns(3)
    a.metric("Pick", pick_no, f"round {rnd}")
    nxt = next((p for p in picks if p >= pick_no), None)
    b.metric("My next pick", nxt or "—")
    c.metric("Up now", "YOU" if on_clock == ss.slot else f"slot {on_clock}")

    dyn = st.toggle("Dynamic advisor", value=True,
                    help="Evaluates your roster, the remaining board, positional "
                         "runs and scarcity — not just raw value.")
    sims = st.select_slider("Rollouts", [40, 70, 120, 200], value=120)

    if st.button("▶ What should I take?", type="primary"):
        with st.spinner("Playing the rest of the draft out…"):
            adv = advise(pool, ss.slot, ss.taken, ss.mine, repl, waiver,
                         pick_no=pick_no, n_sims=sims,
                         scan_sims=80 if dyn else 20, top_k=6)
        ss.advice = adv

    adv = ss.get("advice")
    if adv and adv["pick_no"] == pick_no:
        if dyn:
            for line in adv["reasoning"]:
                st.markdown("- " + line)
            st.markdown("##### Position board")
            pb = pd.DataFrame([{
                "Pos": p["pos"], "Best option": p["best_by_rollout"] or p["best_available"],
                "Season pts": p["rollout_mean"],
                "vs best": None if p["urgency"] < -900 else p["urgency"],
                "Decay by next pick": p["cost_of_waiting"],
                "Left over repl.": p["count_above_replacement"],
                "Survives": (None if p["p_best_survives"] is None
                             else f'{p["p_best_survives"]*100:.0f}%'),
            } for p in adv["positions"]])
            st.dataframe(pb, hide_index=True, use_container_width=True)
            hot = [k for k, v in (adv["runs"] or {}).items() if v.get("hot")]
            if hot:
                st.warning(f"Positional run in progress: {', '.join(hot)}")
        st.markdown("##### Candidates")
        st.dataframe(pd.DataFrame([{
            "Take": r["name"], "Pos": r["pos"], "Proj": r["proj"], "VOR": round(r["vor"], 1),
            "ADP": None if r["adp"] > 900 else r["adp"], "Season pts": r["mean"],
            "Range": f'{r["p10"]:.0f}–{r["p90"]:.0f}',
            "Cost": r["cost_vs_best"], "Flag": r["flag"] or "",
        } for r in adv["recommendations"]]), hide_index=True, use_container_width=True)
    elif adv:
        st.info("Board has moved since that run — click again for a fresh read.")

    st.divider()
    l, r = st.columns([2, 1])
    with l:
        st.markdown("##### Best available")
        avail = [p for p in ranked if p.name not in set(ss.taken)][:40]
        st.dataframe(pd.DataFrame([{
            "Player": p.name, "Pos": f"{p.pos}{p.pos_rank}", "Tier": p.tier,
            "Proj": p.proj, "VOR": p.vor,
            "ADP": None if p.adp > 900 else p.adp, "Bye": p.bye,
            "Flag": p.flag or "",
        } for p in avail]), hide_index=True, use_container_width=True, height=430)
    with r:
        st.markdown("##### My starting lineup")
        mine_p = [by_name[n] for n in ss.mine if n in by_name]
        if mine_p:
            pts, starters = optimal_lineup(mine_p)
            st.dataframe(pd.DataFrame([{"Pos": p.pos, "Player": p.name,
                                        "Proj": round(p.proj, 1)} for p in starters]),
                         hide_index=True, use_container_width=True)
            st.metric("Projected starters", f"{pts:.0f}")
        else:
            st.caption("No players yet.")

# ----------------------------------------------------------------- strategies
with tabs[1]:
    st.markdown("#### Which strategy actually wins in this league?")
    mode = st.radio("Score by", ["Title odds (plays real seasons)",
                                 "Projected lineup points (fast)"], horizontal=True)
    st.caption("Ranking by projected points quietly favours the Balanced plan, because "
               "that is the number it maximises. **Title odds** is the honest comparison.")
    if st.button("▶ Backtest all strategies", type="primary"):
        with st.spinner("Drafting and simulating…"):
            if mode.startswith("Title"):
                res = backtest_seasons(pool, ss.slot, repl, waiver,
                                       n_drafts=20, n_seasons=80)
                ss.strat = ("season", res)
            else:
                res = backtest(pool, ss.slot, repl, waiver, n_sims=120)
                ss.strat = ("points", res)
    if ss.get("strat"):
        kind, res = ss.strat
        if kind == "season":
            st.dataframe(pd.DataFrame([{
                "Strategy": r["name"], "Title odds": f'{r["title_odds"]*100:.1f}%',
                "Playoffs": f'{r["playoff_odds"]*100:.0f}%', "Wins": r["exp_wins"],
                "Spread across drafts":
                    f'{r["worst_draft_title"]*100:.0f}–{r["best_draft_title"]*100:.0f}%',
                "What it is": r["blurb"],
            } for r in res]), hide_index=True, use_container_width=True)
            st.caption("Baseline for a 10-team league is 10%. The spread column is the range "
                       "across individual drafts within one strategy — it is wide, which is the "
                       "real lesson: which players you land matters more than the plan's name.")
        else:
            st.dataframe(pd.DataFrame([{
                "Strategy": r["name"], "Mean lineup": r["mean"],
                "p10–p90": f'{r["p10"]}–{r["p90"]}', "vs best": r["vs_best"],
                "Typical open": r["typical_open"],
            } for r in res]), hide_index=True, use_container_width=True)

    st.divider()
    st.markdown("#### Round-by-round plans for your slot")
    pick = st.selectbox("Strategy", [s.name for s in STRATEGIES])
    strat = next(s for s in STRATEGIES if s.name == pick)
    st.info(f"**{strat.name}** — {strat.blurb}\n\n{strat.rationale}")
    st.dataframe(pd.DataFrame([{"Round": r["round"], "Your pick": f'#{r["pick"]}',
                                "Target": r["target"]}
                               for r in round_plan(strat, ss.slot)]),
                 hide_index=True, use_container_width=True)

# ----------------------------------------------------------------- players
with tabs[2]:
    posf = st.multiselect("Position", ["QB", "RB", "WR", "TE", "K", "D/ST"])
    rows = [p for p in ranked if not posf or p.pos in posf]
    st.dataframe(pd.DataFrame([{
        "Player": p.name, "Pos": f"{p.pos}{p.pos_rank}", "Tier": p.tier, "Proj": p.proj,
        "VOR": p.vor, "ADP": None if p.adp > 900 else p.adp,
        "Source disagreement": p.proj_spread, "SD/wk": p.week_sd,
        "2025": p.actual_2025, "Bye": p.bye, "Flag": p.flag or "",
    } for p in rows[:400]]), hide_index=True, use_container_width=True, height=620)
    st.caption("**Source disagreement** is the gap between ESPN's and Sleeper's projections — "
               "a big number means the player is riskier than one projection suggests. "
               "**SD/wk** is measured from real 2022–2025 game logs.")

# ----------------------------------------------------------------- injuries
with tabs[3]:
    sev = {"SUSPENDED": 0, "IR": 1, "OUT": 1, "PUP": 2, "NFI": 2,
           "Doubtful": 3, "Questionable": 4}
    flagged = sorted([p for p in pool if (p.flag or p.games_missed) and p.adp < 900],
                     key=lambda p: (0 if p.games_missed else 1, sev.get(p.flag, 5), p.adp))
    st.dataframe(pd.DataFrame([{
        "Player": p.name, "Pos": p.pos, "ADP": p.adp, "Status": p.flag or "",
        "Games out": p.games_missed, "Note": p.note or "",
    } for p in flagged]), hide_index=True, use_container_width=True, height=420)
    st.caption("No public feed reports **how many** games a suspension or injury costs — "
               "only a status flag. Set it here and the projection is discounted everywhere.")
    with st.form("override"):
        c1, c2, c3 = st.columns([3, 1, 3])
        nm = c1.selectbox("Player", names)
        gm = c2.number_input("Games out", 0, 17, 0)
        note = c3.text_input("Reason", placeholder="e.g. 2-game suspension")
        if st.form_submit_button("Save", type="primary"):
            store.set_override(nm, int(gm), note) if gm > 0 else store.clear_override(nm)
            load.clear(); st.rerun()

# ----------------------------------------------------------------- draft sim
with tabs[4]:
    n = st.select_slider("Drafts to simulate", [50, 100, 250, 500], value=250)
    if st.button("▶ Run draft simulation", type="primary"):
        with st.spinner("Drafting…"):
            r = simulate(pool, ss.slot, n_sims=n, repl_pts=repl, waiver=waiver)
            store.save_run("draft", {"my_slot": ss.slot, "n_sims": n}, r)
        a, b, c = st.columns(3)
        a.metric("Mean starting lineup", r["mean_starting_points"])
        b.metric("10th–90th percentile", f'{r["p10"]}–{r["p90"]}')
        c.metric("Drafts", r["n_sims"])
        st.markdown("##### Who you end up with, by pick")
        st.dataframe(pd.DataFrame([{
            "Pick": f"#{pk}",
            "Most likely": " · ".join(f"{nm} {round(100*ct/r['n_sims'])}%"
                                      for nm, ct in opts[:4]),
        } for pk, opts in r["pick_frequency"].items()]),
            hide_index=True, use_container_width=True)

# ----------------------------------------------------------------- season sim
with tabs[5]:
    n = st.select_slider("Seasons to simulate", [200, 500, 1000], value=500)
    use_mine = st.checkbox("Use my drafted roster", value=bool(ss.mine))
    if st.button("▶ Run season simulation", type="primary"):
        with st.spinner("Playing seasons…"):
            mine_p = [by_name[x] for x in ss.mine if x in by_name] if use_mine else None
            rosters = build_league(pool, ss.slot, my_players=mine_p,
                                   repl_pts=repl, waiver=waiver)
            r = simulate_season(rosters, ss.slot, n_sims=n)
            store.save_run("season", {"my_slot": ss.slot, "n_sims": n}, r)
        me = next(t for t in r["teams"] if t["is_me"])
        a, b, c = st.columns(3)
        a.metric("My title odds", f'{me["title_odds"]*100:.1f}%', "baseline 10%")
        b.metric("My playoff odds", f'{me["playoff_odds"]*100:.1f}%',
                 f'{me["exp_wins"]}-{14-me["exp_wins"]:.1f}')
        c.metric("Weekly score", r["my_weekly"]["mean"],
                 f'{r["my_weekly"]["p10"]}–{r["my_weekly"]["p90"]}')
        st.dataframe(pd.DataFrame([{
            "Team": t["team"], "Div": t["division"], "Wins": t["exp_wins"],
            "Points": t["exp_points"], "Playoffs": f'{t["playoff_odds"]*100:.0f}%',
            "Title": f'{t["title_odds"]*100:.1f}%',
        } for t in r["teams"]]), hide_index=True, use_container_width=True)
        st.caption("The other nine teams are drafted by the ADP model, so these odds measure "
                   "your roster against *simulated* opponents. Directional, not literal.")
