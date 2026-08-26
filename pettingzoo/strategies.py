"""
Named draft strategies, and a harness that backtests them in THIS league.

Generic fantasy advice ("Zero RB works", "always wait on QB") is written for
12-team leagues with deeper benches. This league is 10 teams, full PPR, one
flex, and only five bench spots -- shallow enough that the usual conclusions
may not hold. So rather than assert anything, every strategy here is played
out hundreds of times against the ADP opponent model and scored.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field

from .draft import (Roster, run_draft, optimal_lineup, greedy_pick,
                    marginal_value, _eligible, OUR_HORIZON)
from .league import N_TEAMS, ROSTER_SIZE, snake_pick_numbers

ANY = ("QB", "RB", "WR", "TE", "K", "D/ST")
FLEXY = ("RB", "WR", "TE")


@dataclass
class Strategy:
    key: str
    name: str
    blurb: str
    # plan[i] = positions allowed in round i+1. Empty tuple == best available.
    plan: list[tuple]
    rationale: str = ""

    def allowed(self, rnd: int) -> tuple:
        if rnd - 1 < len(self.plan):
            return self.plan[rnd - 1] or ANY
        return ANY


# Rounds 13-14 are always K and D/ST; every plan below leaves them implicit.
STRATEGIES: list[Strategy] = [
    Strategy("balanced", "Balanced / Best Available",
             "Take the highest marginal-value player every round, whatever the position.",
             [(), (), (), (), (), (), (), (), (), (), (), ()],
             "No positional dogma. The benchmark every other plan has to beat."),

    Strategy("robust_rb", "Robust RB",
             "Two backs to open, then receivers. Corners the scarcest position.",
             [("RB",), ("RB",), ("WR",), ("WR",), ("RB", "WR"), ("WR", "TE"),
              ("RB", "WR"), ("TE", "QB"), ("QB", "RB", "WR"), (), (), ()],
             "RB scoring falls off a cliff faster than WR, so locking two early "
             "protects the two RB slots plus the flex."),

    Strategy("hero_rb", "Hero RB",
             "One elite back, then hammer receivers until the middle rounds.",
             [("RB",), ("WR",), ("WR",), ("WR", "TE"), ("RB",), ("RB", "WR"),
              ("QB", "TE", "WR"), ("RB", "WR"), (), (), (), ()],
             "Buys one every-week RB advantage without paying twice at a "
             "position with the worst injury rate on the board."),

    Strategy("zero_rb", "Zero RB",
             "Receivers and a tight end early; backs from round five onward.",
             [("WR",), ("WR",), ("WR", "TE"), ("WR", "TE", "QB"), ("RB",),
              ("RB",), ("RB",), ("RB", "WR"), ("QB", "RB", "WR"), (), (), ()],
             "Full PPR inflates receiver floors. The bet is that mid-round RBs "
             "return value once starters ahead of them get hurt."),

    Strategy("elite_te", "Elite TE",
             "Secure a top-two tight end early, then normal best-available.",
             [("RB", "WR"), ("TE",), ("RB", "WR"), ("RB", "WR"), (), (),
              (), (), (), (), (), ()],
             "The TE cliff is the steepest on the board. One start-worthy TE "
             "beats streaming by more than any other single position."),

    Strategy("late_qb", "Late-Round QB",
             "Ignore quarterback until round nine or later.",
             [FLEXY, FLEXY, FLEXY, FLEXY, FLEXY, FLEXY, FLEXY, FLEXY,
              ("QB", "RB", "WR", "TE"), (), (), ()],
             "In a 1-QB league the gap between QB3 and QB12 is small, so the "
             "picks are better spent on positions with real scarcity."),

    Strategy("early_qb", "Early Elite QB",
             "Take one of the top quarterbacks in rounds three to five.",
             [("RB", "WR"), ("RB", "WR"), ("QB",), ("RB", "WR"), ("RB", "WR"),
              (), (), (), (), (), (), ()],
             "Rushing quarterbacks score more total points than anyone. The "
             "question is whether that survives replacement-level pricing."),

    Strategy("wr_heavy", "WR Heavy",
             "Three receivers to open in a full-PPR league.",
             [("WR",), ("WR",), ("WR",), ("RB", "TE"), ("RB",), ("RB", "WR"),
              ("QB", "TE"), (), (), (), (), ()],
             "Receptions are worth a full point and receivers get hurt less "
             "often than backs."),
]
STRATEGY_BY_KEY = {s.key: s for s in STRATEGIES}


def strategy_policy(strat: Strategy):
    """Turn a plan into a pick function compatible with run_draft."""
    def pick(available, roster: Roster, pick_no: int, repl_pts=None, waiver=None):
        rnd = (pick_no - 1) // N_TEAMS + 1
        allowed = strat.allowed(rnd)
        needs = roster.needs(rnd)
        # Respect roster legality first: the plan never gets to skip a K or D/ST
        # we are contractually required to start.
        forced_needs = {p: w for p, w in needs.items() if w > 0}
        pool = [(p, w) for p, w in _eligible(available, forced_needs, None,
                                             horizon=OUR_HORIZON)
                if p.pos in allowed]
        if not pool:
            return greedy_pick(available, roster, pick_no, repl_pts, waiver)
        pool = sorted(pool, key=lambda t: -getattr(t[0], "vor", -999))[:22]
        if repl_pts is None:
            return pool[0][0]
        return max(pool, key=lambda t: marginal_value(roster, t[0], repl_pts, waiver))[0]
    return pick


def backtest(pool, my_slot: int, repl_pts, waiver, n_sims: int = 150,
             seed: int | None = 42, keys: list[str] | None = None) -> list[dict]:
    """Play every strategy from this slot and rank them by resulting lineup."""
    out = []
    picks = snake_pick_numbers(my_slot)
    for strat in STRATEGIES:
        if keys and strat.key not in keys:
            continue
        policy = strategy_policy(strat)
        totals, shapes, first_rounds = [], [], []
        rng = random.Random(seed)
        for _ in range(n_sims):
            rosters = run_draft(pool, my_slot, rng, my_policy=policy,
                                repl_pts=repl_pts, waiver=waiver)
            mine = rosters[my_slot].players
            totals.append(optimal_lineup(mine)[0])
            shape = {}
            for p in mine:
                shape[p.pos] = shape.get(p.pos, 0) + 1
            shapes.append(shape)
            first_rounds.append([p.pos for p in mine[:5]])
        totals.sort()
        n = len(totals)
        avg_shape = {}
        for sh in shapes:
            for k, v in sh.items():
                avg_shape[k] = avg_shape.get(k, 0) + v
        avg_shape = {k: round(v / n, 1) for k, v in sorted(avg_shape.items())}
        # most common opening five positions
        opens = {}
        for fr in first_rounds:
            key = "-".join(fr)
            opens[key] = opens.get(key, 0) + 1
        common_open = max(opens.items(), key=lambda t: t[1])
        out.append({
            "key": strat.key, "name": strat.name, "blurb": strat.blurb,
            "rationale": strat.rationale,
            "mean": round(sum(totals) / n, 1),
            "p10": round(totals[int(0.10 * n)], 1),
            "p90": round(totals[int(0.90 * n)], 1),
            "floor_gap": round(sum(totals) / n - totals[int(0.10 * n)], 1),
            "roster_shape": avg_shape,
            "typical_open": common_open[0],
            "open_pct": round(100 * common_open[1] / n),
            "n_sims": n_sims,
        })
    out.sort(key=lambda r: -r["mean"])
    if out:
        best = out[0]["mean"]
        for r in out:
            r["vs_best"] = round(r["mean"] - best, 1)
    return out


def round_plan(strat: Strategy, my_slot: int) -> list[dict]:
    """Human-readable round-by-round plan with this slot's actual pick numbers."""
    picks = snake_pick_numbers(my_slot)
    rows = []
    for i, pk in enumerate(picks, start=1):
        if i == len(picks):
            target = "K"
        elif i == len(picks) - 1:
            target = "D/ST"
        else:
            a = strat.allowed(i)
            target = "Best available" if a == ANY else " / ".join(a)
        rows.append({"round": i, "pick": pk, "target": target})
    return rows


def backtest_seasons(pool, my_slot: int, repl_pts, waiver, n_drafts: int = 24,
                     n_seasons: int = 80, seed: int | None = 42,
                     keys: list[str] | None = None) -> list[dict]:
    """
    Score strategies on the objective that actually matters: winning.

    Ranking by projected lineup points quietly rigs the contest -- the
    Balanced policy maximises exactly that number, so nothing constrained can
    beat it. Playing real seasons instead introduces weekly variance, injuries
    and bye weeks, which is where the risk profile of a strategy shows up.
    """
    from .season import simulate_season
    out = []
    for strat in STRATEGIES:
        if keys and strat.key not in keys:
            continue
        policy = strategy_policy(strat)
        rng = random.Random(seed)
        titles, playoffs, wins, pts = [], [], [], []
        for d in range(n_drafts):
            rosters_obj = run_draft(pool, my_slot, rng, my_policy=policy,
                                    repl_pts=repl_pts, waiver=waiver)
            rosters = {s: r.players for s, r in rosters_obj.items()}
            res = simulate_season(rosters, my_slot, n_sims=n_seasons, seed=seed + d)
            me = next(t for t in res["teams"] if t["is_me"])
            titles.append(me["title_odds"]); playoffs.append(me["playoff_odds"])
            wins.append(me["exp_wins"]); pts.append(me["exp_points"])
        n = len(titles)
        out.append({
            "key": strat.key, "name": strat.name, "blurb": strat.blurb,
            "rationale": strat.rationale,
            "title_odds": round(sum(titles) / n, 4),
            "playoff_odds": round(sum(playoffs) / n, 4),
            "exp_wins": round(sum(wins) / n, 2),
            "exp_points": round(sum(pts) / n, 1),
            "worst_draft_title": round(min(titles), 4),
            "best_draft_title": round(max(titles), 4),
            "n_drafts": n_drafts, "n_seasons": n_seasons,
        })
    out.sort(key=lambda r: -r["title_odds"])
    if out:
        best = out[0]["title_odds"]
        for r in out:
            r["vs_best"] = round(100 * (r["title_odds"] - best), 1)
    return out
