"""
Season simulation: your roster against the nine others, week by week.

Weekly scores are drawn from a gamma distribution matched to each player's
mean and empirically-measured standard deviation. Gamma rather than normal
because fantasy scoring is right-skewed -- touchdowns produce occasional huge
weeks and a floor at zero, which a normal distribution gets wrong in both tails.
"""
from __future__ import annotations
import random
import numpy as np

from .league import (N_TEAMS, REG_SEASON_WEEKS, PLAYOFF_ROUND1_WEEKS,
                     CHAMPIONSHIP_WEEKS, PLAYOFF_TEAMS, DIVISIONS, TEAM_NAMES,
                     ROSTER_SIZE, MY_TEAM_NAME)
from .draft import optimal_lineup, run_draft, Roster
from .variance import build_model

SLOTS = (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1), ("D/ST", 1))
FLEX_POS = ("RB", "WR", "TE")
ALL_WEEKS = REG_SEASON_WEEKS + PLAYOFF_ROUND1_WEEKS + CHAMPIONSHIP_WEEKS


def make_schedule(n_teams: int = N_TEAMS, weeks: int = 14,
                  divisions: dict | None = None) -> list[list[tuple[int, int]]]:
    """
    14 weeks for 10 teams: division rivals twice, everyone else once, then one
    rotating extra. ESPN's exact generator is not public, so this is a faithful
    approximation -- swap in the real pairings if you export them.
    """
    idx = {name: i for i, name in enumerate(TEAM_NAMES)}
    div = divisions or DIVISIONS
    div_of = {idx[n]: d for d, names in div.items() for n in names}

    pairs = []
    for a in range(n_teams):
        for b in range(a + 1, n_teams):
            reps = 2 if div_of[a] == div_of[b] else 1
            pairs += [(a, b)] * reps

    # circle method over a padded round-robin, then fill from the pair pool
    sched, pool = [], list(pairs)
    rng = random.Random(17)
    rng.shuffle(pool)
    for _ in range(weeks):
        used, week = set(), []
        for pair in list(pool):
            a, b = pair
            if a in used or b in used:
                continue
            week.append(pair); used.add(a); used.add(b); pool.remove(pair)
            if len(week) == n_teams // 2:
                break
        # if the pool ran dry, pair up whoever is left
        if len(week) < n_teams // 2:
            free = [t for t in range(n_teams) if t not in used]
            rng.shuffle(free)
            while len(free) >= 2:
                week.append((free.pop(), free.pop()))
        sched.append(week)
    return sched


def _gamma_params(mean: float, sd: float):
    mean = max(mean, 0.05)
    sd = max(sd, 0.05)
    shape = (mean / sd) ** 2
    scale = (sd ** 2) / mean
    return shape, scale


class SeasonModel:
    """Precomputes per-player weekly distributions for fast repeated sims."""

    def __init__(self, rosters: dict[int, list], model: dict | None = None,
                 weeks=ALL_WEEKS):
        self.model = model or build_model()
        self.weeks = list(weeks)
        self.rosters = rosters
        self.players = []
        seen = set()
        for lst in rosters.values():
            for p in lst:
                if id(p) not in seen:
                    seen.add(id(p)); self.players.append(p)
        self.index = {id(p): i for i, p in enumerate(self.players)}

        n = len(self.players)
        self.mean = np.zeros(n); self.sd = np.zeros(n)
        self.p_active = np.zeros(n); self.bye = np.zeros(n, dtype=int)
        self.pos = []
        games = self.model.get("games", {})
        for i, p in enumerate(self.players):
            self.mean[i] = max(getattr(p, "week_mean", 0.0), 0.01)
            self.sd[i] = max(getattr(p, "week_sd", 1.0), 0.5)
            g = games.get(p.pos if p.pos in games else "RB", {"mean_games": 15.0})
            # vendor projections already assume some missed time; only model the
            # residual availability risk so we do not double-count injuries
            self.p_active[i] = min(1.0, g.get("mean_games", 15.0) / 17.0 + 0.06)
            self.bye[i] = p.bye or 0
            self.pos.append(p.pos)
        self.shape, self.scale = _gamma_params_vec(self.mean, self.sd)

    def draw_week(self, rng: np.random.Generator, week: int):
        pts = rng.gamma(self.shape, self.scale)
        active = rng.random(len(self.players)) < self.p_active
        pts = np.where(active, pts, 0.0)
        pts = np.where(self.bye == week, 0.0, pts)
        return pts, active & (self.bye != week)


def _gamma_params_vec(mean, sd):
    mean = np.maximum(mean, 0.05); sd = np.maximum(sd, 0.05)
    return (mean / sd) ** 2, (sd ** 2) / mean


def _weekly_lineup_points(team_idx, pos_arr, mean_arr, pts, avail):
    """Managers set lineups on expectation, then live with the actual result."""
    by = {}
    for i in team_idx:
        if avail[i]:
            by.setdefault(pos_arr[i], []).append(i)
    for lst in by.values():
        lst.sort(key=lambda i: -mean_arr[i])
    total, used = 0.0, set()
    for pos, n in SLOTS:
        for i in by.get(pos, [])[:n]:
            total += pts[i]; used.add(i)
    flex = [i for i in team_idx
            if avail[i] and pos_arr[i] in FLEX_POS and i not in used]
    if flex:
        best = max(flex, key=lambda i: mean_arr[i])
        total += pts[best]
    return total


def simulate_season(rosters: dict[int, list], my_slot: int, n_sims: int = 400,
                    seed: int | None = None, division_top_seeds: bool = True):
    sm = SeasonModel(rosters)
    rng = np.random.default_rng(seed)
    pos_arr = sm.pos; mean_arr = sm.mean
    team_idx = {s: [sm.index[id(p)] for p in lst] for s, lst in rosters.items()}
    slots = sorted(rosters.keys())
    # The LM sets draft order by hand, so slot -> manager is unknown. Pin OUR
    # team to our slot and label the rest; the other nine are interchangeable
    # for simulation purposes since they are modelled identically.
    others = [n for n in TEAM_NAMES if n != MY_TEAM_NAME]
    name_of, oi = {}, 0
    for s in slots:
        if s == my_slot:
            name_of[s] = MY_TEAM_NAME
        else:
            name_of[s] = others[oi]; oi += 1
    div_of = {}
    for d, names in DIVISIONS.items():
        for nm in names:
            for s, tn in name_of.items():
                if tn == nm:
                    div_of[s] = d

    schedule = make_schedule(len(slots), len(REG_SEASON_WEEKS))
    slot_by_pos = {i: s for i, s in enumerate(slots)}

    agg = {s: {"wins": 0.0, "pf": 0.0, "playoffs": 0, "title": 0, "seeds": []}
           for s in slots}
    my_weekly = []

    for _ in range(n_sims):
        pf = {s: 0.0 for s in slots}
        wins = {s: 0 for s in slots}
        week_scores = {}
        for w in REG_SEASON_WEEKS:
            pts, avail = sm.draw_week(rng, w)
            scores = {s: _weekly_lineup_points(team_idx[s], pos_arr, mean_arr, pts, avail)
                      for s in slots}
            week_scores[w] = scores
            for s in slots:
                pf[s] += scores[s]
            for a, b in schedule[w - 1]:
                sa, sb = slot_by_pos[a], slot_by_pos[b]
                if scores[sa] > scores[sb]:
                    wins[sa] += 1
                elif scores[sb] > scores[sa]:
                    wins[sb] += 1
            my_weekly.append(scores[my_slot])

        # ---- seeding ---------------------------------------------------
        order = sorted(slots, key=lambda s: (-wins[s], -pf[s]))
        if division_top_seeds:
            champs = []
            for d in DIVISIONS:
                in_div = [s for s in slots if div_of.get(s) == d]
                if in_div:
                    champs.append(sorted(in_div, key=lambda s: (-wins[s], -pf[s]))[0])
            champs.sort(key=lambda s: (-wins[s], -pf[s]))
            rest = [s for s in order if s not in champs]
            seeds = champs + rest
        else:
            seeds = order
        bracket = seeds[:PLAYOFF_TEAMS]
        for rank, s in enumerate(seeds, 1):
            agg[s]["seeds"].append(rank)
        for s in bracket:
            agg[s]["playoffs"] += 1

        # ---- playoffs: week 15 semis, weeks 16+17 two-week final --------
        def play(a, b, weeks):
            ta = tb = 0.0
            for w in weeks:
                pts, avail = sm.draw_week(rng, w)
                ta += _weekly_lineup_points(team_idx[a], pos_arr, mean_arr, pts, avail)
                tb += _weekly_lineup_points(team_idx[b], pos_arr, mean_arr, pts, avail)
            return a if ta >= tb else b

        if len(bracket) >= 4:
            w1 = play(bracket[0], bracket[3], PLAYOFF_ROUND1_WEEKS)
            w2 = play(bracket[1], bracket[2], PLAYOFF_ROUND1_WEEKS)
            champ = play(w1, w2, CHAMPIONSHIP_WEEKS)
            agg[champ]["title"] += 1

        for s in slots:
            agg[s]["wins"] += wins[s]
            agg[s]["pf"] += pf[s]

    out = []
    for s in slots:
        a = agg[s]
        out.append({
            "slot": s, "team": name_of[s], "division": div_of.get(s),
            "is_me": s == my_slot,
            "exp_wins": round(a["wins"] / n_sims, 2),
            "exp_points": round(a["pf"] / n_sims, 1),
            "playoff_odds": round(a["playoffs"] / n_sims, 3),
            "title_odds": round(a["title"] / n_sims, 3),
            "avg_seed": round(sum(a["seeds"]) / len(a["seeds"]), 2),
            "roster": [(p.name, p.pos, round(p.proj, 1)) for p in rosters[s]],
        })
    out.sort(key=lambda r: -r["title_odds"])
    mw = sorted(my_weekly)
    return {
        "n_sims": n_sims,
        "teams": out,
        "my_weekly": {
            "mean": round(float(np.mean(my_weekly)), 1),
            "p10": round(mw[int(0.10 * len(mw))], 1),
            "p90": round(mw[int(0.90 * len(mw))], 1),
        },
    }


def build_league(pool, my_slot, my_players=None, seed=None, repl_pts=None, waiver=None):
    """Draft the other nine teams around a (possibly fixed) roster of ours."""
    rng = random.Random(seed)
    if my_players:
        forced = {}
        from .league import snake_pick_numbers
        for pick_no, p in zip(snake_pick_numbers(my_slot), my_players):
            forced[pick_no] = p
        rosters = run_draft(pool, my_slot, rng, forced=forced,
                            repl_pts=repl_pts, waiver=waiver)
    else:
        rosters = run_draft(pool, my_slot, rng, repl_pts=repl_pts, waiver=waiver)
    return {s: r.players for s, r in rosters.items()}
