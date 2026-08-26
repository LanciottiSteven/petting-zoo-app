"""
Monte Carlo draft simulation.

Opponents are modelled from real mock-draft behaviour: each pick is drawn by
perturbing every available player's ADP by its own measured standard deviation
and taking the best result that fits that team's roster needs. That reproduces
the way real drafts drift without assuming anyone picks perfectly.

Our own picks are evaluated by one-ply lookahead with rollouts: for each
candidate, play the rest of the draft out many times and keep the candidate
with the best average resulting starting lineup.
"""
from __future__ import annotations
import random, math
from dataclasses import dataclass, field

from .league import (STARTERS, FLEX_ELIGIBLE, ROSTER_MAX, ROSTER_SIZE,
                     N_TEAMS, slot_for_pick, snake_pick_numbers)
from .pool import norm_name

TOTAL_PICKS = N_TEAMS * ROSTER_SIZE
# Nobody sane takes a kicker in round 3. Enforce the social convention.
EARLIEST_ROUND = {"K": 13, "D/ST": 11}
# How far down the ADP board a pick is drawn from. Well past any realistic
# reach, but small enough that rollouts stay fast on a 90-second clock.
OPPONENT_HORIZON = 40
OUR_HORIZON = 55
STARTER_TARGET = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "D/ST": 1}
# A realistic end-state roster for a 14-man bench-light team.
ROSTER_TARGET = {"QB": 1, "RB": 5, "WR": 5, "TE": 1, "K": 1, "D/ST": 1}
# Hard caps. With only 5 bench spots you cannot afford a third QB or a second
# kicker, and both are freely streamable, so nobody should ever roster one.
ROSTER_CAP = {"QB": 2, "RB": 6, "WR": 6, "TE": 2, "K": 1, "D/ST": 1}


@dataclass
class Roster:
    slot: int
    players: list = field(default_factory=list)

    def count(self, pos: str) -> int:
        return sum(1 for p in self.players if p.pos == pos)

    def needs(self, pick_round: int) -> dict[str, float]:
        """Soft positional need weights for this team right now."""
        w = {}
        remaining = ROSTER_SIZE - len(self.players)
        for pos, target in ROSTER_TARGET.items():
            have = self.count(pos)
            if have >= min(ROSTER_CAP.get(pos, 99), ROSTER_MAX.get(pos, 99)):
                w[pos] = 0.0
                continue
            starters_left = max(0, STARTER_TARGET[pos] - have)
            # must-fill urgency rises as the draft runs out
            urgency = 1.0 + (2.5 * starters_left if remaining <= 4 else 0.35 * starters_left)
            depth = 1.0 if have < target else 0.35
            w[pos] = urgency * depth
        for pos, rd in EARLIEST_ROUND.items():
            if pick_round < rd:
                w[pos] = 0.0
        # force the last picks to complete a legal lineup
        must = [p for p in ("QB", "TE", "K", "D/ST")
                if self.count(p) < STARTER_TARGET[p]]
        if must and remaining <= len(must):
            for pos in w:
                w[pos] = 1.0 if pos in must else 0.0
        return w

    def lineup_points(self) -> float:
        return optimal_lineup(self.players)[0]


def optimal_lineup(players):
    """Best legal starting lineup by projection. Returns (points, starters)."""
    by = {}
    for p in players:
        by.setdefault(p.pos, []).append(p)
    for lst in by.values():
        lst.sort(key=lambda p: -p.proj)
    starters, used = [], set()
    for pos, n in (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1), ("D/ST", 1)):
        for p in by.get(pos, [])[:n]:
            starters.append(p); used.add(id(p))
    flex_pool = [p for p in players
                 if p.pos in FLEX_ELIGIBLE and id(p) not in used]
    if flex_pool:
        best = max(flex_pool, key=lambda p: p.proj)
        starters.append(best); used.add(id(best))
    return round(sum(p.proj for p in starters), 2), starters


def _eligible(available, needs, taken, horizon=None):
    """Need-eligible players. `available` is ADP-sorted, so `horizon` takes the
    top N by ADP -- nobody drafts the 300th-ranked player at pick 30, and
    scanning the whole board was costing ~10x in the rollouts."""
    out = []
    for p in available:
        if taken and id(p) in taken:
            continue
        w = needs.get(p.pos, 0.0)
        if w > 0:
            out.append((p, w))
            if horizon and len(out) >= horizon:
                break
    return out


def opponent_pick(available, roster, pick_no, rng, noise=1.0):
    """Draw a pick by perturbing ADP by each player's own measured stdev."""
    rd = (pick_no - 1) // N_TEAMS + 1
    needs = roster.needs(rd)
    cands = _eligible(available, needs, None, horizon=OPPONENT_HORIZON)
    if not cands:
        cands = [(p, 1.0) for p in available[:OPPONENT_HORIZON]]
    best, best_score = None, 1e18
    for p, w in cands:
        adp = p.adp if p.adp < 900 else 250.0
        sd = (p.adp_stdev or max(4.0, adp * 0.18)) * noise
        # need acts as a small pull, not an override
        score = rng.gauss(adp, sd) - 6.0 * math.log(max(w, 0.05))
        if score < best_score:
            best, best_score = p, score
    return best


def lineup_with_replacement(players, repl_pts, waiver=None) -> float:
    """
    Projected starting-lineup points, where any starter slot we cannot fill is
    charged at replacement level. This is what makes the policy understand
    saturation: a second TE improves nothing once the TE slot is filled and the
    FLEX prefers a back or receiver.
    """
    by = {}
    for p in players:
        by.setdefault(p.pos, []).append(p)
    for lst in by.values():
        lst.sort(key=lambda p: -p.proj)
    empty = waiver if waiver is not None else repl_pts
    total, used = 0.0, set()
    for pos, n in (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1), ("D/ST", 1)):
        got = by.get(pos, [])[:n]
        for p in got:
            total += p.proj
            used.add(id(p))
        total += (n - len(got)) * empty.get(pos, 0.0)
    flex = [p for p in players if p.pos in FLEX_ELIGIBLE and id(p) not in used]
    total += max((p.proj for p in flex), default=max(
        empty.get("RB", 0.0), empty.get("WR", 0.0)))
    return total


# Weight on raw VOR, which keeps late picks sensible once the lineup is full
# (marginal starter value is ~0 for every bench body, but depth still matters
# for byes and the ~3 games the average starter misses).
DEPTH_WEIGHT = 0.22


def marginal_value(roster, cand, repl_pts, waiver=None) -> float:
    base = lineup_with_replacement(roster.players, repl_pts, waiver)
    after = lineup_with_replacement(roster.players + [cand], repl_pts, waiver)
    # Bench depth only pays at flex-eligible positions. A backup QB, K or D/ST
    # is a wasted bench spot in a 10-team league -- waiver QBs project ~220.
    depth = DEPTH_WEIGHT if cand.pos in FLEX_ELIGIBLE else 0.0
    return (after - base) + depth * max(0.0, getattr(cand, "vor", 0.0))


def greedy_pick(available, roster, pick_no, repl_pts=None, waiver=None):
    """Our policy inside rollouts: best marginal lineup gain that fits a need."""
    rd = (pick_no - 1) // N_TEAMS + 1
    needs = roster.needs(rd)
    cands = _eligible(available, needs, None, horizon=OUR_HORIZON)
    if not cands:
        cands = [(p, 1.0) for p in available[:OUR_HORIZON]]
    cands = sorted(cands, key=lambda t: -getattr(t[0], "vor", -999))[:22]
    if repl_pts is None:
        return max(cands, key=lambda t: getattr(t[0], "vor", -999))[0]
    return max(cands, key=lambda t: marginal_value(roster, t[0], repl_pts, waiver))[0]


def run_draft(pool, my_slot, rng, my_policy=None, forced=None,
              n_teams=N_TEAMS, repl_pts=None, waiver=None):
    """Play one full draft. `forced` maps our pick number -> player to take."""
    available = sorted([p for p in pool if p.proj > 0 and not getattr(p, 'flag_excluded', False)],
                       key=lambda p: p.adp)
    rosters = {s: Roster(s) for s in range(1, n_teams + 1)}
    taken = set()
    forced = forced or {}
    for pick_no in range(1, n_teams * ROSTER_SIZE + 1):
        slot = slot_for_pick(pick_no, n_teams)
        roster = rosters[slot]
        pool_now = [p for p in available if id(p) not in taken]
        if not pool_now:
            break
        if slot == my_slot:
            pick = forced.get(pick_no) or (my_policy or greedy_pick)(pool_now, roster, pick_no, repl_pts, waiver)
            if id(pick) in taken:
                pick = greedy_pick(pool_now, roster, pick_no, repl_pts, waiver)
        else:
            pick = opponent_pick(pool_now, roster, pick_no, rng)
        taken.add(id(pick))
        roster.players.append(pick)
    return rosters


def simulate(pool, my_slot, n_sims=300, seed=None, forced=None,
             repl_pts=None, waiver=None):
    """Aggregate many drafts from our slot. Returns summary + pick frequencies."""
    rng = random.Random(seed)
    for p in pool:
        p.flag_excluded = bool(p.flag in ("SUSPENDED",) and p.games_missed >= 17)
    totals, rosters_seen = [], []
    freq: dict[int, dict[str, int]] = {}
    my_picks = snake_pick_numbers(my_slot, n_teams=N_TEAMS)
    for _ in range(n_sims):
        rosters = run_draft(pool, my_slot, rng, forced=forced, repl_pts=repl_pts, waiver=waiver)
        mine = rosters[my_slot]
        pts, starters = optimal_lineup(mine.players)
        totals.append(pts)
        rosters_seen.append(mine.players)
        for pick_no, p in zip(my_picks, mine.players):
            freq.setdefault(pick_no, {}).setdefault(p.name, 0)
            freq[pick_no][p.name] += 1
    totals.sort()
    n = len(totals)
    return {
        "slot": my_slot,
        "n_sims": n_sims,
        "mean_starting_points": round(sum(totals) / n, 1),
        "p10": round(totals[int(0.10 * n)], 1),
        "p50": round(totals[int(0.50 * n)], 1),
        "p90": round(totals[int(0.90 * n)], 1),
        "pick_frequency": {
            str(k): sorted(v.items(), key=lambda t: -t[1])[:6] for k, v in sorted(freq.items())
        },
        "sample_roster": [(p.name, p.pos, round(p.proj, 1)) for p in rosters_seen[-1]],
    }


def availability_at_pick(pool, my_slot, pick_no, n_sims=200, seed=None, top_n=25):
    """P(player is still on the board) at one of our picks. Drives draft-day calls."""
    rng = random.Random(seed)
    for p in pool:
        p.flag_excluded = False
    counts: dict[str, int] = {}
    cands = sorted([p for p in pool if p.proj > 0 and p.adp < 900], key=lambda p: p.adp)[:120]
    names = {p.name for p in cands}
    for _ in range(n_sims):
        available = sorted([p for p in pool if p.proj > 0], key=lambda p: p.adp)
        rosters = {s: Roster(s) for s in range(1, N_TEAMS + 1)}
        taken = set()
        for pk in range(1, pick_no):
            slot = slot_for_pick(pk, N_TEAMS)
            now = [p for p in available if id(p) not in taken]
            if not now:
                break
            pick = opponent_pick(now, rosters[slot], pk, rng)
            taken.add(id(pick))
            rosters[slot].players.append(pick)
        gone = {p.name for p in pool if id(p) in taken}
        for nm in names:
            if nm not in gone:
                counts[nm] = counts.get(nm, 0) + 1
    out = [(nm, round(counts.get(nm, 0) / n_sims, 3)) for nm in names]
    out.sort(key=lambda t: -t[1])
    byadp = {p.name: p for p in cands}
    return [{"name": nm, "pos": byadp[nm].pos, "adp": byadp[nm].adp,
             "vor": getattr(byadp[nm], "vor", 0), "p_available": pr}
            for nm, pr in out if pr > 0.02][:top_n]


# --------------------------------------------------------------- live draft
def recommend(pool, my_slot, taken_names, repl_pts, waiver, my_roster_names=None,
              pick_no=None, n_sims=120, top_k=8, seed=None, cover_positions=False):
    """
    The draft-day call. For each plausible candidate on the board, force it as
    our pick and roll the rest of the draft out `n_sims` times, then report the
    distribution of the resulting starting lineup. This prices in what the pick
    costs us at our NEXT turn, which a static cheat sheet cannot do.
    """
    rng = random.Random(seed)
    # Names arrive by copy/paste on draft night: curly apostrophes, suffixes and
    # accents all differ from ESPN's spelling. Always match on normalised names.
    taken = {norm_name(n) for n in taken_names}
    mine = {norm_name(n) for n in (my_roster_names or [])}
    board = [p for p in pool if p.proj > 0 and norm_name(p.name) not in taken]
    my_players = [p for p in pool if norm_name(p.name) in mine]

    if pick_no is None:
        pick_no = len(taken_names) + 1
    rd = (pick_no - 1) // N_TEAMS + 1

    roster = Roster(my_slot, list(my_players))
    needs = roster.needs(rd)
    eligible = [p for p in board if needs.get(p.pos, 0) > 0]
    eligible.sort(key=lambda p: -marginal_value(roster, p, repl_pts, waiver))
    cands = eligible[:top_k]
    if cover_positions:
        # Guarantee the best option at every position we can still roster gets
        # evaluated, so position ranking and player choice share one metric.
        have = {c.pos for c in cands}
        for pos in ("QB", "RB", "WR", "TE"):
            if pos in have:
                continue
            best = next((p for p in eligible if p.pos == pos), None)
            if best:
                cands.append(best)

    results = []
    for cand in cands:
        totals = []
        for _ in range(n_sims):
            sim_taken = set(taken) | {norm_name(cand.name)}
            avail = [p for p in pool if p.proj > 0 and norm_name(p.name) not in sim_taken]
            r = Roster(my_slot, list(my_players) + [cand])
            others = {s: Roster(s) for s in range(1, N_TEAMS + 1) if s != my_slot}
            used = set()
            for pk in range(pick_no + 1, N_TEAMS * ROSTER_SIZE + 1):
                slot = slot_for_pick(pk, N_TEAMS)
                now = [p for p in avail if id(p) not in used]
                if not now:
                    break
                if slot == my_slot:
                    if len(r.players) >= ROSTER_SIZE:
                        continue
                    pick = greedy_pick(now, r, pk, repl_pts, waiver)
                    r.players.append(pick)
                else:
                    pick = opponent_pick(now, others[slot], pk, rng)
                    others[slot].players.append(pick)
                used.add(id(pick))
            totals.append(optimal_lineup(r.players)[0])
        totals.sort()
        n = len(totals)
        results.append({
            "name": cand.name, "pos": cand.pos, "team": cand.team,
            "proj": cand.proj, "vor": getattr(cand, "vor", 0.0),
            "adp": cand.adp, "tier": getattr(cand, "tier", None),
            "bye": cand.bye, "flag": cand.flag,
            "mean": round(sum(totals) / n, 1),
            "p10": round(totals[int(0.10 * n)], 1),
            "p90": round(totals[int(0.90 * n)], 1),
        })
    results.sort(key=lambda r: -r["mean"])
    if results:
        best = results[0]["mean"]
        for r in results:
            r["cost_vs_best"] = round(r["mean"] - best, 1)
    return results
