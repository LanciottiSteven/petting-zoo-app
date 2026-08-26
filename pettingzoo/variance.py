"""
Empirical week-to-week variance, learned from real nflverse game logs.

Monte Carlo is only as honest as its variance model. Rather than assuming a
normal distribution around a projection, we measure how much real players at a
given production level actually bounce around week to week, and how often they
miss games.
"""
from __future__ import annotations
import json, math, statistics
from pathlib import Path

from . import sources as S
from .scoring import score_named_stats

CACHE = S.DATA_DIR / "variance_model.json"
TRAIN_SEASONS = (2022, 2023, 2024, 2025)
POSITIONS = ("QB", "RB", "WR", "TE")
GAMES = 17


def _player_seasons(seasons=TRAIN_SEASONS):
    """-> {(player_id, season): {'pos':..,'weeks':[pts,...]}}"""
    out = {}
    for yr in seasons:
        try:
            rows = S.fetch_weekly_stats(yr)
        except Exception:
            continue
        for r in rows:
            if r.get("season_type") != "REG":
                continue
            pos = r.get("position")
            if pos not in POSITIONS:
                continue
            key = (r["player_id"], yr)
            rec = out.setdefault(key, {"pos": pos, "weeks": [], "name": r.get("player_display_name")})
            rec["weeks"].append(score_named_stats(r))
    return out


def build_model(min_games: int = 6, force: bool = False) -> dict:
    if CACHE.exists() and not force:
        return json.loads(CACHE.read_text())

    ps = _player_seasons()
    # ---- 1. how weekly SD scales with weekly mean, per position ----------
    fits, games_dist = {}, {}
    for pos in POSITIONS:
        pts = [(statistics.mean(v["weeks"]), statistics.pstdev(v["weeks"]))
               for v in ps.values()
               if v["pos"] == pos and len(v["weeks"]) >= min_games]
        if len(pts) < 20:
            fits[pos] = {"a": 2.0, "b": 0.55, "n": 0}
            continue
        # least squares  sd = a + b*mean
        xs = [m for m, _ in pts]
        ys = [s for _, s in pts]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs) or 1e-9
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        a = my - b * mx
        fits[pos] = {"a": round(a, 4), "b": round(b, 4), "n": n}

        # ---- 2. games-played distribution (availability risk) ------------
        # Selecting on games-played would bias this badly (you cannot post a big
        # season total while injured). So we select on PER-GAME quality among
        # players with a real sample, then measure how many games they managed.
        starters_n = {"QB": 32, "RB": 60, "WR": 90, "TE": 32}[pos]
        cands = [v for v in ps.values()
                 if v["pos"] == pos and len(v["weeks"]) >= 4]
        cands.sort(key=lambda v: -statistics.mean(v["weeks"]))
        per_season = max(1, starters_n)
        keep = cands[: per_season * len(TRAIN_SEASONS)]
        sample = [len(v["weeks"]) for v in keep] or [
            len(v["weeks"]) for v in ps.values() if v["pos"] == pos]
        games_dist[pos] = {
            "mean_games": round(sum(sample) / len(sample), 2),
            "p_full": round(sum(1 for g in sample if g >= 16) / len(sample), 3),
            "hist": _hist(sample),
            "n": len(sample),
        }

    model = {"sd_fit": fits, "games": games_dist, "seasons": list(TRAIN_SEASONS)}
    CACHE.write_text(json.dumps(model, indent=1))
    return model


def _hist(sample):
    """Normalised games-played histogram, 1..17, for resampling."""
    counts = [0] * (GAMES + 1)
    for g in sample:
        counts[min(g, GAMES)] += 1
    total = sum(counts) or 1
    return [round(c / total, 5) for c in counts]


def weekly_sd(model: dict, pos: str, weekly_mean: float) -> float:
    f = model["sd_fit"].get(pos) or {"a": 2.0, "b": 0.55}
    return max(1.0, f["a"] + f["b"] * max(0.0, weekly_mean))


if __name__ == "__main__":
    m = build_model(force=True)
    print(json.dumps(m["sd_fit"], indent=1))
    print("\nposition   mean_games  p_full_season   n")
    for pos, g in m["games"].items():
        print(f"{pos:<10}{g['mean_games']:>10}{g['p_full']:>15}{g['n']:>6}")
    print("\nimplied weekly SD at various weekly means:")
    print(f"{'pos':<6}" + "".join(f"{x:>9}" for x in (5, 10, 15, 20, 25)))
    for pos in POSITIONS:
        print(f"{pos:<6}" + "".join(f"{weekly_sd(m,pos,x):>9.1f}" for x in (5,10,15,20,25)))
