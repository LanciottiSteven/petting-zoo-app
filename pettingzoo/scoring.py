"""League scoring engine. Turns raw component stats into Petting Zoo points."""
from __future__ import annotations
from .league import (ESPN_STAT_POINTS, KICKER_POINTS, DST_EVENT_POINTS,
                     DST_POINTS_ALLOWED_TIERS, DST_YARDS_ALLOWED_TIERS)


def score_espn_stats(stats: dict[str, float]) -> float:
    """Score an ESPN component-stat map. Exact vs ESPN's own applied totals."""
    return sum(ESPN_STAT_POINTS[k] * v for k, v in stats.items()
               if k in ESPN_STAT_POINTS)


# Sleeper/nflverse use named fields rather than numeric ids.
NAMED_POINTS = {
    "passing_yards": 0.04, "pass_yd": 0.04,
    "passing_tds": 4.0, "pass_td": 4.0,
    "passing_interceptions": -2.0, "pass_int": -2.0, "interceptions": -2.0,
    "pass_2pt": 2.0, "passing_2pt_conversions": 2.0,
    "rushing_yards": 0.1, "rush_yd": 0.1,
    "rushing_tds": 6.0, "rush_td": 6.0,
    "rush_2pt": 2.0, "rushing_2pt_conversions": 2.0,
    "receiving_yards": 0.1, "rec_yd": 0.1,
    "receiving_tds": 6.0, "rec_td": 6.0,
    "receptions": 1.0, "rec": 1.0,
    "rec_2pt": 2.0, "receiving_2pt_conversions": 2.0,
    "fumbles_lost": -2.0, "fum_lost": -2.0,
    "rushing_fumbles_lost": -2.0, "receiving_fumbles_lost": -2.0,
    "sack_fumbles_lost": -2.0,
    "special_teams_tds": 6.0, "def_st_td": 6.0,
}


def score_named_stats(stats: dict) -> float:
    total = 0.0
    for k, v in stats.items():
        w = NAMED_POINTS.get(k)
        if w is None:
            continue
        try:
            total += w * float(v or 0)
        except (TypeError, ValueError):
            continue
    return total


def _tier(value: float, tiers) -> float:
    for upper, pts in tiers:
        if value <= upper:
            return pts
    return tiers[-1][1]


def score_kicker(pat_made=0.0, fg_0_39=0.0, fg_40_49=0.0,
                 fg_50_59=0.0, fg_60_plus=0.0, fg_missed=0.0) -> float:
    k = KICKER_POINTS
    return (pat_made * k["pat_made"] + fg_0_39 * k["fg_0_39"]
            + fg_40_49 * k["fg_40_49"] + fg_50_59 * k["fg_50_59"]
            + fg_60_plus * k["fg_60_plus"] + fg_missed * k["fg_missed"])


def score_dst(points_allowed: float, yards_allowed: float, sacks=0.0,
              interceptions=0.0, fumbles_recovered=0.0, safeties=0.0,
              blocked_kicks=0.0, def_tds=0.0, return_tds=0.0) -> float:
    """Note: this league scores D/ST on BOTH points allowed and yards allowed,
    which roughly doubles D/ST variance versus a points-only league."""
    e = DST_EVENT_POINTS
    total = (sacks * e["sack"] + interceptions * e["interception"]
             + fumbles_recovered * e["fumble_recovered"] + safeties * e["safety"]
             + blocked_kicks * e["blocked_kick"] + def_tds * e["def_td"]
             + return_tds * e["return_td"])
    total += _tier(points_allowed, DST_POINTS_ALLOWED_TIERS)
    total += _tier(yards_allowed, DST_YARDS_ALLOWED_TIERS)
    return total
