"""
Draft valuation: replacement level, VOR, tiers, and per-game distributions.

Raw projected points are the wrong draft currency in a 1-QB league: the 12th
best QB is nearly as good as the 5th, while the 12th best RB is a cliff below
the 5th. VOR (value over replacement) prices that in.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

from .league import STARTERS, FLEX_ELIGIBLE, N_TEAMS
from .variance import weekly_sd, build_model

# How the single FLEX spot is expected to be filled in a full-PPR league.
FLEX_SHARE = {"RB": 0.45, "WR": 0.50, "TE": 0.05}
STARTABLE_WEEKS = 14          # regular season length drives roster demand


def replacement_ranks(n_teams: int = N_TEAMS) -> dict[str, int]:
    """Index (1-based) of the replacement-level player at each position."""
    ranks = {}
    for pos, n in STARTERS.items():
        if pos == "FLEX":
            continue
        ranks[pos] = n * n_teams
    for pos, share in FLEX_SHARE.items():
        ranks[pos] = ranks.get(pos, 0) + round(STARTERS["FLEX"] * n_teams * share)
    # K and D/ST are streamed off waivers every week, so their true
    # replacement is near the TOP of the position, not the 10th man. Pricing
    # them against rank ~10 would make a kicker look like a mid-round RB.
    # Preseason K/D-ST projections also have close to zero predictive power.
    ranks["K"] = 5
    ranks["D/ST"] = 5
    # A little streaming slack exists at QB and TE too, but far less.
    ranks["QB"] = ranks.get("QB", n_teams) + 2
    ranks["TE"] = ranks.get("TE", n_teams) + 1
    return ranks


# What you can actually still get at a position once the draft is over. Leaving
# a starter slot empty must be charged at THIS level, not at preseason
# replacement -- by round 8 the preseason "replacement" player is long gone.
WAIVER_RANK = {"QB": 26, "RB": 52, "WR": 58, "TE": 24, "K": 14, "D/ST": 14}


def waiver_levels(pool) -> dict[str, float]:
    out = {}
    for pos, rank in WAIVER_RANK.items():
        lst = sorted([p for p in pool if p.pos == pos and p.proj > 0],
                     key=lambda p: -p.proj)
        if lst:
            out[pos] = lst[min(rank, len(lst)) - 1].proj
    return out


def compute_valuation(pool, n_teams: int = N_TEAMS, model: dict | None = None):
    """Annotates each player with vor, weekly mean/sd, tier. Returns replacement pts."""
    model = model or build_model()
    by_pos: dict[str, list] = {}
    for p in pool:
        if p.proj > 0:
            by_pos.setdefault(p.pos, []).append(p)
    for lst in by_pos.values():
        lst.sort(key=lambda p: -p.proj)

    ranks = replacement_ranks(n_teams)
    repl_pts = {}
    for pos, lst in by_pos.items():
        idx = min(max(ranks.get(pos, len(lst)), 1), len(lst)) - 1
        repl_pts[pos] = lst[idx].proj

    for pos, lst in by_pos.items():
        base = repl_pts[pos]
        for i, p in enumerate(lst):
            p.pos_rank = i + 1
            p.vor = round(p.proj - base, 2)
            p.week_mean = round(p.proj / STARTABLE_WEEKS, 3)
            p.week_sd = round(weekly_sd(model, pos if pos in ("QB","RB","WR","TE") else "TE",
                                        p.week_mean), 3)
        _assign_tiers(lst)

    for p in pool:
        if not hasattr(p, "vor"):
            p.vor, p.pos_rank, p.tier = -999.0, 999, 99
            p.week_mean, p.week_sd = 0.0, 0.0
    return repl_pts


def _assign_tiers(lst, max_tiers: int = 12):
    """Tier breaks where the projection gap is unusually large."""
    if len(lst) < 3:
        for p in lst:
            p.tier = 1
        return
    gaps = [lst[i].proj - lst[i + 1].proj for i in range(len(lst) - 1)]
    positive = [g for g in gaps if g > 0]
    if not positive:
        for p in lst:
            p.tier = 1
        return
    mean_g = sum(positive) / len(positive)
    sd_g = (sum((g - mean_g) ** 2 for g in positive) / len(positive)) ** 0.5
    threshold = mean_g + 0.9 * sd_g
    tier = 1
    lst[0].tier = 1
    for i in range(1, len(lst)):
        if gaps[i - 1] >= threshold and tier < max_tiers:
            tier += 1
        lst[i].tier = tier


@dataclass
class PosSummary:
    pos: str
    replacement_pts: float
    replacement_rank: int
    tier1_count: int
    cliff_after: int | None


def positional_report(pool, repl_pts, n_teams: int = N_TEAMS) -> list[PosSummary]:
    ranks = replacement_ranks(n_teams)
    out = []
    for pos in ("QB", "RB", "WR", "TE", "K", "D/ST"):
        lst = sorted([p for p in pool if p.pos == pos and p.proj > 0],
                     key=lambda p: -p.proj)
        if not lst:
            continue
        t1 = sum(1 for p in lst if getattr(p, "tier", 99) == 1)
        cliff = None
        for i in range(min(len(lst) - 1, 40)):
            if lst[i].proj - lst[i + 1].proj > 18:
                cliff = i + 1
                break
        out.append(PosSummary(pos, round(repl_pts.get(pos, 0), 1),
                              ranks.get(pos, 0), t1, cliff))
    return out
