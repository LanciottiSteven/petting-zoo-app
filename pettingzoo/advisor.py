"""
The adaptive advisor: what to take next, and why.

Three questions get answered together on every pick:

  1. Which single player maximises the finished roster?  -> forward rollouts
  2. Which POSITION is about to get scarce?               -> cost-of-waiting scan
  3. What is the board doing right now?                   -> positional run detection

The rollout answers the decision; the scarcity scan supplies the reasoning, so
the recommendation is never a bare name you have to take on faith.
"""
from __future__ import annotations
import random
from collections import Counter

from .draft import (Roster, opponent_pick, greedy_pick, marginal_value,
                    optimal_lineup, recommend, N_TEAMS, ROSTER_SIZE,
                    STARTER_TARGET, ROSTER_CAP)
from .league import slot_for_pick, snake_pick_numbers, FLEX_ELIGIBLE
from .pool import norm_name

TRACKED = ("QB", "RB", "WR", "TE")


def _next_pick_after(my_slot: int, pick_no: int) -> int | None:
    for pk in snake_pick_numbers(my_slot):
        if pk > pick_no:
            return pk
    return None


def forward_scan(pool, my_slot: int, taken: set, pick_no: int, next_pick: int,
                 n_sims: int = 80, seed: int | None = None):
    """
    Play the gap between this pick and our next one many times and record what
    survives. Returns, per position, the distribution of the best player still
    on the board when we are back on the clock.
    """
    rng = random.Random(seed)
    board = sorted([p for p in pool if p.proj > 0 and norm_name(p.name) not in taken],
                   key=lambda p: p.adp)
    tracked = board[:80]
    survivors: dict[str, list[float]] = {pos: [] for pos in TRACKED}
    survival_count: Counter = Counter()
    trials = 0

    for _ in range(n_sims):
        used = set()
        rosters = {s: Roster(s) for s in range(1, N_TEAMS + 1) if s != my_slot}
        for pk in range(pick_no, next_pick):
            slot = slot_for_pick(pk, N_TEAMS)
            if slot == my_slot:
                continue
            now = [p for p in board if id(p) not in used]
            if not now:
                break
            pick = opponent_pick(now, rosters[slot], pk, rng)
            used.add(id(pick))
            rosters[slot].players.append(pick)
        trials += 1
        left = [p for p in board if id(p) not in used]
        for pos in TRACKED:
            best = max((p.proj for p in left if p.pos == pos), default=0.0)
            survivors[pos].append(best)
        for p in tracked:
            if id(p) not in used:
                survival_count[p.name] += 1

    # Seed every tracked name at zero. A player who NEVER survives must report
    # 0%, not a blank -- "he will not be there" is the single most actionable
    # thing this scan produces, and a dash reads as missing data.
    survival = {p.name: 0.0 for p in tracked}
    for k, v in survival_count.items():
        survival[k] = round(v / max(trials, 1), 3)
    return {"survivors": survivors, "survival": survival, "trials": trials}


def detect_runs(pool, taken_order: list[str], window: int = 8) -> dict:
    """Is the room currently hammering one position?"""
    by_name = {norm_name(p.name): p for p in pool}
    recent = [by_name.get(norm_name(n)) for n in taken_order[-window:]]
    recent = [p for p in recent if p]
    counts = Counter(p.pos for p in recent)
    n = len(recent) or 1
    # Baseline share of picks each position normally takes in the early/mid draft.
    baseline = {"RB": 0.34, "WR": 0.38, "TE": 0.11, "QB": 0.12, "K": 0.02, "D/ST": 0.03}
    runs = {}
    for pos, c in counts.items():
        share = c / n
        runs[pos] = {
            "count": c, "of": n, "share": round(share, 2),
            "hot": share >= baseline.get(pos, 0.2) * 1.8 and c >= 3,
        }
    return runs


def advise(pool, my_slot: int, taken_names: list[str], my_roster_names: list[str],
           repl_pts, waiver, pick_no: int | None = None, n_sims: int = 100,
           scan_sims: int = 80, top_k: int = 6, seed: int | None = None) -> dict:
    taken = {norm_name(n) for n in taken_names}
    mine_norm = {norm_name(n) for n in my_roster_names}
    my_players = [p for p in pool if norm_name(p.name) in mine_norm]
    if pick_no is None:
        pick_no = len(taken_names) + 1
    rnd = (pick_no - 1) // N_TEAMS + 1
    nxt = _next_pick_after(my_slot, pick_no)

    roster = Roster(my_slot, list(my_players))
    board = [p for p in pool if p.proj > 0 and norm_name(p.name) not in taken]

    # ---- 1. best player, by forward rollout -----------------------------
    picks = recommend(pool, my_slot, taken_names, repl_pts, waiver,
                      my_roster_names=my_roster_names, pick_no=pick_no,
                      n_sims=n_sims, top_k=top_k, seed=seed,
                      cover_positions=True)
    # best rollout outcome achievable at each position -- one metric for both
    # the player choice and the position ranking
    best_by_pos: dict[str, dict] = {}
    for r in picks:
        cur = best_by_pos.get(r["pos"])
        if cur is None or r["mean"] > cur["mean"]:
            best_by_pos[r["pos"]] = r

    # ---- 2. what survives to our next pick ------------------------------
    scan = {"survivors": {}, "survival": {}, "trials": 0}
    if nxt:
        scan = forward_scan(pool, my_slot, taken, pick_no, nxt,
                            n_sims=scan_sims, seed=seed)

    # ---- 3. per-position urgency ----------------------------------------
    needs = roster.needs(rnd)
    positions = []
    for pos in TRACKED:
        here = sorted([p for p in board if p.pos == pos], key=lambda p: -p.proj)
        if not here:
            continue
        best_now = here[0]
        surv = scan["survivors"].get(pos) or []
        exp_later = sum(surv) / len(surv) if surv else best_now.proj
        cost_of_waiting = round(best_now.proj - exp_later, 1)

        have = roster.count(pos)
        need_starters = max(0, STARTER_TARGET.get(pos, 0) - have)
        capped = have >= ROSTER_CAP.get(pos, 99)

        # how thin is the current tier?
        tier = getattr(best_now, "tier", None)
        tier_left = sum(1 for p in here if getattr(p, "tier", None) == tier)
        p_best_survives = scan["survival"].get(best_now.name)

        positions.append({
            "pos": pos,
            "best_available": best_now.name,
            "best_proj": best_now.proj,
            "best_vor": round(getattr(best_now, "vor", 0.0), 1),
            "tier": tier, "tier_left": tier_left,
            "count_above_replacement": sum(
                1 for p in here if p.proj > repl_pts.get(pos, 0)),
            "exp_best_at_next_pick": round(exp_later, 1),
            "cost_of_waiting": cost_of_waiting,
            "p_best_survives": p_best_survives,
            "starters_needed": need_starters,
            "capped": capped,
            "marginal_now": round(marginal_value(roster, best_now, repl_pts, waiver), 1),
        })
    # Urgency IS the rollout outcome: how good is the finished roster if we
    # take this position now. Expressed relative to the best position so the
    # numbers read as "points given up by going here instead".
    ref = max((r["mean"] for r in best_by_pos.values()), default=0.0)
    for row in positions:
        hit = best_by_pos.get(row["pos"])
        row["rollout_mean"] = hit["mean"] if hit else None
        row["best_by_rollout"] = hit["name"] if hit else None
        if row["capped"] or needs.get(row["pos"], 0) <= 0 or not hit:
            row["urgency"] = -999.0
        else:
            row["urgency"] = round(hit["mean"] - ref, 1)
    positions.sort(key=lambda r: -r["urgency"])

    runs = detect_runs(pool, taken_names)
    reasoning = _explain(picks, positions, runs, roster, rnd, pick_no, nxt, scan)

    return {
        "pick_no": pick_no, "round": rnd, "next_pick": nxt,
        "recommendations": picks,
        "positions": positions,
        "runs": runs,
        "reasoning": reasoning,
        "roster_summary": _roster_summary(roster, repl_pts),
        "scan_trials": scan["trials"],
    }


def _roster_summary(roster: Roster, repl_pts) -> dict:
    counts = Counter(p.pos for p in roster.players)
    missing = [pos for pos, n in STARTER_TARGET.items() if counts.get(pos, 0) < n]
    pts, starters = optimal_lineup(roster.players)
    return {
        "counts": dict(counts),
        "filled": len(roster.players),
        "missing_starters": missing,
        "lineup_points": pts,
        "starters": [(p.name, p.pos, round(p.proj, 1)) for p in starters],
    }


def _explain(picks, positions, runs, roster, rnd, pick_no, nxt, scan) -> list[str]:
    """Plain-language reasoning. This is the part that makes the call trustworthy."""
    out = []
    if not picks:
        return ["No legal candidates left on the board."]
    top = picks[0]
    second = picks[1] if len(picks) > 1 else None

    lead = f"Take **{top['name']}** ({top['pos']}"
    if top.get("tier"):
        lead += f", tier {top['tier']}"
    lead += ")."
    if second and second.get("cost_vs_best") is not None:
        gap = abs(second["cost_vs_best"])
        if gap < 4:
            lead += (f" It is close though — {second['name']} costs only "
                     f"{gap:.1f} projected points, so take whichever you prefer.")
        else:
            lead += (f" The next best option, {second['name']}, gives up "
                     f"{gap:.1f} projected points.")
    out.append(lead)

    urgent = [p for p in positions if p["urgency"] > -900]
    if urgent:
        u = urgent[0]
        if u["cost_of_waiting"] >= 12:
            msg = (f"{u['pos']} is the position under pressure: the best one left "
                   f"is {u['best_available']} at {u['best_proj']:.0f} projected, but "
                   f"by pick {nxt} you should expect only "
                   f"{u['exp_best_at_next_pick']:.0f} — about "
                   f"{u['cost_of_waiting']:.0f} points of decay.")
            if u.get("p_best_survives") is not None:
                msg += (f" {u['best_available']} survives to your next pick only "
                        f"{u['p_best_survives']*100:.0f}% of the time.")
            out.append(msg)
        deep = [p for p in urgent if p["cost_of_waiting"] < 6
                and p["count_above_replacement"] >= 8]
        if deep:
            names = ", ".join(f"{p['pos']} ({p['count_above_replacement']} left "
                              f"above replacement)" for p in deep[:2])
            out.append(f"You can safely wait on {names} — the drop-off between now "
                       f"and your next pick is small.")

    hot = [pos for pos, r in runs.items() if r.get("hot")]
    if hot:
        out.append(f"Positional run in progress: {', '.join(hot)} "
                   f"({runs[hot[0]]['count']} of the last {runs[hot[0]]['of']} picks). "
                   f"Expect the next tier there to go faster than ADP implies.")

    missing = [pos for pos, n in STARTER_TARGET.items()
               if roster.count(pos) < n]
    left = ROSTER_SIZE - len(roster.players)
    if missing and left <= len(missing) + 2:
        out.append(f"Roster warning: still missing {', '.join(missing)} with only "
                   f"{left} picks left. Fill required slots now.")
    elif missing and rnd >= 9:
        out.append(f"Still unfilled: {', '.join(missing)}.")

    if top.get("flag"):
        out.append(f"Note: {top['name']} is flagged **{top['flag']}** — confirm "
                   f"status before taking, and set games-missed if it is real.")
    return out
