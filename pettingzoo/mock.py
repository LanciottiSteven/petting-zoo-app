"""
Mock draft plan: what to take at each of your turns, and what to fall back to.

Plays the draft forward from a chosen seat. At every one of that seat's picks it
stops, evaluates the real board, and records the primary target plus backups —
each with the reason it is there. The opponents in between are drawn from ADP
the same way the live agent models them, so the board you see at round 7 is a
plausible board, not a static cheat sheet.
"""
from __future__ import annotations
import random

from .league import N_TEAMS, ROSTER_SIZE, snake_pick_numbers, slot_for_pick
from .draft import (Roster, opponent_pick, greedy_pick, marginal_value,
                    optimal_lineup, STARTER_TARGET, ROSTER_CAP)
from .draftroom import LiveDraft
from .pool import norm_name


def _why(cand, *, is_primary, best_value, cand_value, pos_left, needs,
         seen_pct, next_at_pos=None, primary_pos=None) -> str:
    """
    A reason specific to THIS player at THIS pick. The first version repeated
    'fills your open RB' on every row, which told you nothing about why one
    option beat another.
    """
    bits = []
    if is_primary:
        gap = best_value - cand_value
        if cand.pos in needs:
            bits.append(f"fills your open {cand.pos}")
        if getattr(cand, "tier", 99) <= 2:
            bits.append(f"tier {cand.tier} {cand.pos}")
        if next_at_pos is not None and next_at_pos >= 12:
            bits.append(f"{next_at_pos:.0f} pts clear of the next {cand.pos}")
        if seen_pct <= 45:
            bits.append(f"only there {seen_pct}% of the time — have the backup ready")
    else:
        cost = best_value - cand_value
        if cost >= 1:
            bits.append(f"costs ~{cost:.0f} pts vs the primary")
        else:
            bits.append("essentially interchangeable with the primary")
        if seen_pct >= 85:
            bits.append(f"but available {seen_pct}% of the time — the safer plan")
        # only worth saying when it changes the shape of the roster
        if cand.pos in needs and cand.pos != primary_pos:
            bits.append(f"covers {cand.pos} instead")

    if pos_left is not None and pos_left <= 4:
        bits.append(f"only {pos_left} startable {cand.pos} left")
    if cand.flag:
        bits.append(f"flagged {cand.flag}")
    elif cand.proj_spread and cand.proj_spread > 40:
        bits.append(f"sources differ by {cand.proj_spread:.0f} pts — volatile")
    return "; ".join(bits[:3]) or f"best value left ({cand.vor:.0f} over replacement)"


def build_plan(pool, my_slot: int, repl_pts, waiver, n_boards: int = 24,
               seed: int | None = 11) -> dict:
    """
    Play `n_boards` full drafts. Each board keeps its OWN roster state, so the
    sequence of picks inside it is coherent; then we report the board whose
    finished lineup is closest to the median, annotated with how often each
    player was actually available across all boards.

    (Averaging candidate values across boards — the obvious first approach —
    produces an incoherent plan: it mixes roster states, so it happily
    recommends four running backs and then two quarterbacks.)
    """
    my_picks = snake_pick_numbers(my_slot, N_TEAMS, ROSTER_SIZE)
    board_all = sorted([p for p in pool if p.proj > 0], key=lambda p: p.adp)
    avail_ct: dict[int, dict[str, int]] = {pk: {} for pk in my_picks}
    boards = []

    for b in range(n_boards):
        rng = random.Random((seed or 0) + b)
        used: set[int] = set()
        rosters = {s: Roster(s) for s in range(1, N_TEAMS + 1)}
        mine = rosters[my_slot]
        seq = []
        for pk in range(1, N_TEAMS * ROSTER_SIZE + 1):
            now = [p for p in board_all if id(p) not in used]
            if not now:
                break
            slot = slot_for_pick(pk, N_TEAMS)
            if slot == my_slot:
                rd = (pk - 1) // N_TEAMS + 1
                needs = mine.needs(rd)
                cands = [p for p in now if needs.get(p.pos, 0) > 0][:45]
                # rank against THIS board's roster, so the sequence stays coherent
                scored = sorted(((marginal_value(mine, p, repl_pts, waiver), p)
                                 for p in cands), key=lambda t: -t[0])
                for _, c in scored[:6]:
                    avail_ct[pk][c.name] = avail_ct[pk].get(c.name, 0) + 1
                if scored:
                    pick = scored[0][1]
                    seq.append({"pick": pk, "round": rd,
                                "chosen": pick, "chosen_value": round(scored[0][0], 1),
                                "alts": [(round(v, 1), p) for v, p in scored[1:4]],
                                "needs_before": [q for q, n in STARTER_TARGET.items()
                                                 if mine.count(q) < n]})
                else:
                    pick = greedy_pick(now, mine, pk, repl_pts, waiver)
            else:
                pick = opponent_pick(now, rosters[slot], pk, rng)
            used.add(id(pick))
            rosters[slot].players.append(pick)
        pts, _ = optimal_lineup(mine.players)
        boards.append((pts, seq, list(mine.players)))

    boards.sort(key=lambda t: t[0])
    pts, seq, roster = boards[len(boards) // 2]        # the median board

    # how many startable players remain at each position, for the "only N left" note
    by_pos_left = {}
    for pos in ("QB", "RB", "WR", "TE"):
        by_pos_left[pos] = sum(1 for p in pool
                               if p.pos == pos and p.proj > repl_pts.get(pos, 0))

    rounds = []
    for step in seq:
        c = step["chosen"]
        best_v = step["chosen_value"]
        seen = lambda x: round(100 * avail_ct[step["pick"]].get(x.name, 0) / n_boards)
        # gap to the next player at the same position on this board
        same_pos = [v for v, p in step["alts"] if p.pos == c.pos]
        next_gap = (c.proj - max((p.proj for _, p in step["alts"] if p.pos == c.pos),
                                 default=c.proj))
        prim = {
            "player": c, "value": best_v, "seen_pct": seen(c),
            "why": _why(c, is_primary=True, best_value=best_v, cand_value=best_v,
                        pos_left=by_pos_left.get(c.pos), needs=step["needs_before"],
                        seen_pct=seen(c), next_at_pos=next_gap),
        }
        backups = [{
            "player": p, "value": v, "seen_pct": seen(p),
            "why": _why(p, is_primary=False, best_value=best_v, cand_value=v,
                        pos_left=by_pos_left.get(p.pos), needs=step["needs_before"],
                        seen_pct=seen(p), primary_pos=c.pos),
        } for v, p in step["alts"]]
        rounds.append({"round": step["round"], "pick": step["pick"],
                       "primary": prim, "backups": backups,
                       "needs_before": step["needs_before"]})

    lpts, starters = optimal_lineup(roster)
    shape: dict[str, int] = {}
    for p in roster:
        shape[p.pos] = shape.get(p.pos, 0) + 1
    return {
        "my_slot": my_slot, "picks": my_picks, "n_boards": n_boards,
        "rounds": rounds,
        "roster": [(p.name, p.pos, round(p.proj, 1)) for p in roster],
        "roster_shape": shape,
        "projected_lineup": [(p.name, p.pos, round(p.proj, 1)) for p in starters],
        "projected_points": lpts,
        "spread": (round(boards[0][0], 1), round(boards[-1][0], 1)),
    }
