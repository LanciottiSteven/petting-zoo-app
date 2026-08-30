"""
Live draft state and the opponent-aware agent.

The earlier advisor simulated the other nine teams. Here they are *known*: every
pick is recorded against the team that made it, so the agent can read real
roster composition rather than guessing. That changes the advice — a position is
urgent when the teams picking before you actually still need it, not merely when
ADP says it is popular.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .league import (N_TEAMS, ROSTER_SIZE, STARTERS, TEAM_NAMES, MY_TEAM_NAME,
                     slot_for_pick, snake_pick_numbers, FLEX_ELIGIBLE,
                     DRAFT_ORDER, MY_SLOT)
from .draft import (Roster, optimal_lineup, marginal_value, recommend,
                    STARTER_TARGET, ROSTER_CAP, lineup_with_replacement,
                    bye_penalty)
from .advisor import forward_scan, detect_runs, TRACKED
from .pool import norm_name

STARTER_SLOTS = (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1), ("D/ST", 1))


@dataclass
class Pick:
    overall: int
    player: str
    slot: int            # 1..n_teams — which draft seat made the pick


@dataclass
class LiveDraft:
    my_slot: int = MY_SLOT
    n_teams: int = N_TEAMS
    roster_size: int = ROSTER_SIZE
    started: bool = False
    picks: list[Pick] = field(default_factory=list)
    # seat -> manager name; seat order is unknown until the LM posts it, so the
    # user labels seats themselves and we only pin our own.
    seat_names: dict[int, str] = field(default_factory=lambda: dict(DRAFT_ORDER))

    # ---------------------------------------------------------------- basics
    @property
    def pick_no(self) -> int:
        return len(self.picks) + 1

    @property
    def round_no(self) -> int:
        return (self.pick_no - 1) // self.n_teams + 1

    @property
    def on_the_clock(self) -> int:
        return slot_for_pick(self.pick_no, self.n_teams)

    @property
    def my_picks(self) -> list[int]:
        return snake_pick_numbers(self.my_slot, self.n_teams, self.roster_size)

    @property
    def next_pick(self) -> int | None:
        return next((p for p in self.my_picks if p >= self.pick_no), None)

    @property
    def following_pick(self) -> int | None:
        """
        Our next turn STRICTLY after the current pick. When we are on the clock
        `next_pick` is this very pick, so any look-ahead keyed on it spans zero
        picks — which silently zeroed out both the decay and the teams-needing
        columns exactly when they matter most.
        """
        return next((p for p in self.my_picks if p > self.pick_no), None)

    @property
    def picks_until_my_turn(self) -> int:
        nxt = self.next_pick
        return 0 if nxt is None else max(0, nxt - self.pick_no)

    def name_for(self, slot: int) -> str:
        if slot == self.my_slot:
            return MY_TEAM_NAME
        return self.seat_names.get(slot) or f"Seat {slot}"

    def taken_names(self) -> list[str]:
        return [p.player for p in self.picks]

    def roster_of(self, slot: int) -> list[str]:
        return [p.player for p in self.picks if p.slot == slot]

    def my_roster(self) -> list[str]:
        return self.roster_of(self.my_slot)

    def add(self, player: str, slot: int | None = None) -> None:
        self.picks.append(Pick(self.pick_no, player, slot or self.on_the_clock))

    def undo(self) -> str | None:
        return self.picks.pop().player if self.picks else None

    # ---------------------------------------------------------------- needs
    def needs_of(self, slot: int, by_name: dict) -> dict[str, int]:
        """Starter slots this team has not filled yet."""
        counts: dict[str, int] = {}
        flex_pool = 0
        for nm in self.roster_of(slot):
            p = by_name.get(norm_name(nm))
            if not p:
                continue
            counts[p.pos] = counts.get(p.pos, 0) + 1
        need = {}
        for pos, n in STARTER_SLOTS:
            short = max(0, n - counts.get(pos, 0))
            if short:
                need[pos] = short
        # flex is filled by any surplus RB/WR/TE
        surplus = sum(max(0, counts.get(p, 0) - dict(STARTER_SLOTS)[p])
                      for p in FLEX_ELIGIBLE)
        if surplus < 1:
            need["FLEX"] = 1
        return need

    def league_demand(self, by_name: dict, before_pick: int | None = None) -> dict[str, int]:
        """
        How many teams picking between now and our next turn still need each
        position. This is the number that actually predicts a run.
        """
        before_pick = before_pick or self.following_pick or (self.pick_no + self.n_teams)
        seats = [slot_for_pick(pk, self.n_teams)
                 for pk in range(self.pick_no, before_pick)]
        seats = [s for s in seats if s != self.my_slot]
        # Every team "needs" a K and a D/ST all draft long, but nobody takes one
        # before the last couple of rounds, so counting them as demand in round 2
        # is noise that drowns out the signal.
        late = self.round_no >= 11
        demand: dict[str, int] = {}
        for s in set(seats):
            weight = seats.count(s)
            for pos, short in self.needs_of(s, by_name).items():
                if pos == "FLEX":
                    continue
                if pos in ("K", "D/ST") and not late:
                    continue
                demand[pos] = demand.get(pos, 0) + short * weight
        return demand

    def league_table(self, by_name: dict) -> list[dict]:
        rows = []
        for slot in range(1, self.n_teams + 1):
            names = self.roster_of(slot)
            counts: dict[str, int] = {}
            pts = 0.0
            for nm in names:
                p = by_name.get(norm_name(nm))
                if p:
                    counts[p.pos] = counts.get(p.pos, 0) + 1
                    pts += p.proj
            need = self.needs_of(slot, by_name)
            rows.append({
                "slot": slot, "team": self.name_for(slot), "is_me": slot == self.my_slot,
                "n": len(names), "counts": counts, "needs": need,
                "proj": round(pts, 1),
                "players": names,
            })
        return rows


# -------------------------------------------------------------------- agent
def agent(pool, live: LiveDraft, repl_pts, waiver, n_sims: int = 110,
          scan_sims: int = 70, top_k: int = 6) -> dict:
    """
    What to do on this pick, and why — grounded in who actually still needs what.
    """
    by_name = {norm_name(p.name): p for p in pool}
    taken = live.taken_names()
    mine = live.my_roster()
    my_players = [by_name[norm_name(n)] for n in mine if norm_name(n) in by_name]
    roster = Roster(live.my_slot, list(my_players))

    picks = recommend(pool, live.my_slot, taken, repl_pts, waiver,
                      my_roster_names=mine, pick_no=live.pick_no,
                      n_sims=n_sims, top_k=top_k, cover_positions=True)

    # look ahead to the turn AFTER this one, so "what survives" and "who needs
    # what" cover the picks that actually happen between now and our next choice
    nxt = live.following_pick
    taken_norm = {norm_name(n) for n in taken}
    scan = {"survivors": {}, "survival": {}, "trials": 0}
    if nxt and nxt > live.pick_no:
        scan = forward_scan(pool, live.my_slot, taken_norm, live.pick_no, nxt,
                            n_sims=scan_sims)

    demand = live.league_demand(by_name)
    board = [p for p in pool if p.proj > 0 and norm_name(p.name) not in taken_norm]

    best_by_pos = {}
    for r in picks:
        cur = best_by_pos.get(r["pos"])
        if cur is None or r["mean"] > cur["mean"]:
            best_by_pos[r["pos"]] = r
    ref = max((r["mean"] for r in best_by_pos.values()), default=0.0)

    my_needs = live.needs_of(live.my_slot, by_name)
    positions = []
    for pos in TRACKED + ("K", "D/ST"):
        here = sorted([p for p in board if p.pos == pos], key=lambda p: -p.proj)
        if not here:
            continue
        best = here[0]
        surv = scan["survivors"].get(pos) or []
        later = sum(surv) / len(surv) if surv else best.proj
        above_repl = sum(1 for p in here if p.proj > repl_pts.get(pos, 0))
        hit = best_by_pos.get(pos)
        capped = roster.count(pos) >= min(ROSTER_CAP.get(pos, 99), 99)
        positions.append({
            "pos": pos,
            "best": best.name, "best_proj": best.proj,
            "best_by_rollout": hit["name"] if hit else best.name,
            "rollout_mean": hit["mean"] if hit else None,
            "urgency": None if (capped or not hit) else round(hit["mean"] - ref, 1),
            "decay": round(best.proj - later, 1),
            "left_above_repl": above_repl,
            "survives": scan["survival"].get(best.name),
            "teams_needing": demand.get(pos, 0),
            "i_need": my_needs.get(pos, 0),
            "capped": capped,
        })
    positions.sort(key=lambda r: (r["urgency"] is None, -(r["urgency"] or -1e9)))

    runs = detect_runs(pool, taken)
    rationale = _rationale(picks, positions, runs, live, by_name, demand, roster)
    alts = _alternatives(picks, positions, demand, by_name)

    return {
        "pick_no": live.pick_no, "round": live.round_no,
        "next_pick": nxt, "picks_until": (nxt - live.pick_no) if nxt else 0,
        "recommendations": picks,
        "positions": positions,
        "demand": demand,
        "runs": runs,
        "rationale": rationale,
        "alternatives": alts,
        "my_needs": my_needs,
        "lineup_points": optimal_lineup(my_players)[0] if my_players else 0.0,
    }


def _slot_story(cand_pos, roster, by_name) -> str:
    """
    Say plainly what this pick does to the lineup. Without this the agent reads
    like autodraft — it names a winner but never explains that a third running
    back only upgrades your flex while quarterback is an empty starting slot.
    """
    have = roster.count(cand_pos)
    need = STARTER_TARGET.get(cand_pos, 0)
    if have < need:
        n = need - have
        return (f"it fills an empty starting {cand_pos} slot"
                + (f" (you still need {n})" if n > 1 else ""))
    if cand_pos in FLEX_ELIGIBLE:
        return (f"your {cand_pos} starters are set, so this upgrades the FLEX"
                if have == need else
                f"you already have {have} {cand_pos}s — this is flex or bench depth")
    return f"you already have your starting {cand_pos}"


def _rationale(picks, positions, runs, live, by_name, demand, roster) -> list[str]:
    out = []
    if not picks:
        return ["Nothing legal left on the board."]
    top = picks[0]
    p = by_name.get(norm_name(top["name"]))

    line = f"**Take {top['name']}** — {top['pos']}"
    if top.get("tier"):
        line += f", tier {top['tier']}"
    if p is not None:
        line += f", {p.proj:.0f} projected points"
    line += "."
    out.append(line)

    # why THIS position and not the obvious alternative
    out.append(f"Roster logic: {_slot_story(top['pos'], roster, by_name)}.")
    other = next((r for r in picks[1:] if r["pos"] != top["pos"]), None)
    if other:
        gap = abs(other.get("cost_vs_best") or 0)
        story = _slot_story(other["pos"], roster, by_name)
        if gap < 4:
            out.append(f"Nearly a coin flip with {other['name']} ({other['pos']}), where "
                       f"{story} — only {gap:.1f} points between them, so take the one you "
                       f"believe in.")
        else:
            out.append(f"The best {other['pos']} left, {other['name']}, is {gap:.1f} points "
                       f"behind: {story}.")
    same = next((r for r in picks[1:] if r["pos"] == top["pos"]), None)
    if same:
        out.append(f"Next best at the same position is {same['name']} "
                   f"({abs(same.get('cost_vs_best') or 0):.1f} behind).")

    live_pos = [r for r in positions if r["urgency"] is not None]
    pressure_pos = None
    if live_pos:
        u = live_pos[0]
        pressure_pos = u["pos"]
        bits = []
        if u["decay"] >= 12:
            bits.append(f"the best {u['pos']} left drops about {u['decay']:.0f} points "
                        f"by your next turn")
        if u["teams_needing"]:
            gap = (live.following_pick or live.pick_no) - live.pick_no
            bits.append(f"{u['teams_needing']} unfilled {u['pos']} slots sit among the "
                        f"{gap} picks before your next turn")
        if u.get("survives") == 0:
            bits.append(f"{u['best']} never lasts to your next pick in simulation")
        elif u.get("survives") is not None and u["survives"] < 0.35:
            bits.append(f"{u['best']} survives only {u['survives']*100:.0f}% of the time")
        if bits:
            out.append(f"{u['pos']} is the pressure point: " + "; ".join(bits) + ".")

    # never list the pressure point as safe to wait on — saying "TE is the
    # pressure point" and "you can wait on TE" in the same breath is nonsense
    safe = [r for r in live_pos
            if r["decay"] < 8 and r["left_above_repl"] >= 8
            and r["pos"] != pressure_pos and not r["teams_needing"]]
    if safe:
        out.append("You can wait on " + ", ".join(
            f"{r['pos']} (best one only falls {r['decay']:.0f} pts, "
            f"{r['left_above_repl']} left above replacement)" for r in safe[:2]) + ".")

    hot = [k for k, v in (runs or {}).items() if v.get("hot")]
    if hot:
        r = runs[hot[0]]
        out.append(f"⚠︎ Run under way on {', '.join(hot)} — "
                   f"{r['count']} of the last {r['of']} picks. Expect that tier to clear "
                   f"faster than ADP implies.")

    left = live.roster_size - len(live.my_roster())
    must = [pos for pos, n in STARTER_TARGET.items() if roster.count(pos) < n]
    if must and left <= len(must) + 1:
        out.append(f"⚠︎ Roster warning: still missing {', '.join(must)} with only "
                   f"{left} picks left. Fill required slots now.")

    # bye stacking is easy to miss by eye and costs real starts
    cand = by_name.get(norm_name(top["name"]))
    if cand is not None:
        before = bye_penalty(roster.players)
        after = bye_penalty(roster.players + [cand])
        if after > before:
            out.append(f"⚠︎ Bye clash: {top['name']} is on bye week {cand.bye}, same as a "
                       f"starter you already hold — costs roughly {after - before:.0f} points "
                       f"of replacement-level starts that week.")

    if top.get("flag"):
        out.append(f"⚠︎ {top['name']} is flagged **{top['flag']}** — confirm his status "
                   f"before locking this in.")
    return out


def _alternatives(picks, positions, demand, by_name) -> list[dict]:
    """Other names worth a look, each with the reason it is on the list."""
    out = []
    for r in picks[1:]:
        p = by_name.get(norm_name(r["name"]))
        posrow = next((x for x in positions if x["pos"] == r["pos"]), None)
        why = []
        if posrow:
            if posrow["teams_needing"] >= 3:
                why.append(f"{posrow['teams_needing']} unfilled {r['pos']} slots ahead of you")
            if posrow["decay"] >= 12:
                why.append(f"{r['pos']} decays {posrow['decay']:.0f} pts by your next pick")
            if posrow["left_above_repl"] <= 4:
                why.append(f"only {posrow['left_above_repl']} startable {r['pos']} left")
        if p is not None and p.proj_spread and p.proj_spread > 35:
            why.append("projection sources disagree — higher variance")
        if r.get("flag"):
            why.append(f"flagged {r['flag']}")
        if not why:
            why.append("similar value, different position mix")
        out.append({**r, "why": "; ".join(why)})
    return out
