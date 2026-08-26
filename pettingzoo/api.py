"""FastAPI backend. Serves the draft app and runs simulations on demand."""
from __future__ import annotations
import time, threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store, sources
from . import pool as poolmod
from .pool import build_pool, norm_name
from .valuation import compute_valuation, waiver_levels, positional_report, replacement_ranks
from .draft import simulate, recommend, availability_at_pick, optimal_lineup
from .season import build_league, simulate_season
from .strategies import (STRATEGIES, STRATEGY_BY_KEY, backtest, backtest_seasons,
                         round_plan)
from .advisor import advise
from .league import (LEAGUE_NAME, N_TEAMS, ROSTER_SIZE, STARTERS, MY_TEAM_NAME,
                     DRAFT_DATE, snake_pick_numbers)

WEB = Path(__file__).resolve().parent / "web"
app = FastAPI(title="Petting Zoo Draft Assistant")

_lock = threading.Lock()
_state = {"pool": None, "repl": None, "waiver": None, "built_at": 0.0}


POOL_TTL = 1800   # rebuild every 30 min so per-source TTLs actually get checked


def get_pool(force: bool = False):
    with _lock:
        expired = (time.time() - _state["built_at"]) > POOL_TTL
        if _state["pool"] is None or force or expired:
            poolmod.GAMES_MISSED_OVERRIDES = {
                k: (v[0], v[1]) for k, v in store.get_overrides().items()}
            p = build_pool(force=force)
            repl = compute_valuation(p)
            _state.update(pool=p, repl=repl, waiver=waiver_levels(p),
                          built_at=time.time())
        return _state["pool"], _state["repl"], _state["waiver"]


def player_json(p):
    return {
        "id": p.espn_id, "name": p.name, "pos": p.pos, "team": p.team, "bye": p.bye,
        "proj": p.proj, "proj_espn": p.proj_espn, "proj_sleeper": p.proj_sleeper,
        "proj_spread": p.proj_spread, "actual_2025": p.actual_2025,
        "games_2025": p.games_2025, "adp": p.adp, "adp_espn": p.adp_espn,
        "adp_ffc": p.adp_ffc, "adp_stdev": p.adp_stdev, "auction": p.auction,
        "pct_owned": p.pct_owned, "vor": getattr(p, "vor", 0.0),
        "pos_rank": getattr(p, "pos_rank", None), "tier": getattr(p, "tier", None),
        "week_mean": getattr(p, "week_mean", 0.0), "week_sd": getattr(p, "week_sd", 0.0),
        "flag": p.flag, "injury_status": p.injury_status,
        "games_missed": p.games_missed, "note": p.note,
    }


# ------------------------------------------------------------------ models
class RefreshReq(BaseModel):
    force: bool = True


class RecommendReq(BaseModel):
    my_slot: int
    taken: list[str] = []
    my_roster: list[str] = []
    pick_no: int | None = None
    n_sims: int = 120
    top_k: int = 8
    save: bool = False


class DraftSimReq(BaseModel):
    my_slot: int
    n_sims: int = 200
    seed: int | None = None
    save: bool = True
    label: str | None = None


class SeasonSimReq(BaseModel):
    my_slot: int
    my_roster: list[str] = []
    n_sims: int = 400
    seed: int | None = None
    save: bool = True
    label: str | None = None


class OverrideReq(BaseModel):
    name: str
    games_missed: int
    note: str = ""


class StrategyReq(BaseModel):
    my_slot: int
    mode: str = "season"          # 'season' (title odds) | 'points' (fast)
    n_sims: int = 120
    n_drafts: int = 20
    n_seasons: int = 80
    save: bool = True
    label: str | None = None


class AdviseReq(BaseModel):
    my_slot: int
    taken: list[str] = []
    my_roster: list[str] = []
    pick_no: int | None = None
    n_sims: int = 100
    scan_sims: int = 80
    top_k: int = 6
    save: bool = False


class StateReq(BaseModel):
    taken: list[str] = []
    my_roster: list[str] = []
    pick_no: int = 1
    my_slot: int | None = None


# ------------------------------------------------------------------ routes
@app.get("/api/league")
def league_info():
    p, repl, waiver = get_pool()
    return {
        "name": LEAGUE_NAME, "my_team": MY_TEAM_NAME, "n_teams": N_TEAMS,
        "roster_size": ROSTER_SIZE, "starters": STARTERS, "draft_date": DRAFT_DATE,
        "replacement_points": {k: round(v, 1) for k, v in repl.items()},
        "replacement_ranks": replacement_ranks(),
        "waiver_levels": {k: round(v, 1) for k, v in waiver.items()},
        "my_slot": store.get_setting("my_draft_slot"),
        "built_at": _state["built_at"],
        "data_status": sources.data_status(),
        "positional": [pr.__dict__ for pr in positional_report(p, repl)],
    }


@app.get("/api/players")
def players(limit: int = 400, pos: str | None = None):
    p, _, _ = get_pool()
    lst = [x for x in p if x.proj > 0]
    if pos:
        lst = [x for x in lst if x.pos == pos]
    lst.sort(key=lambda x: -getattr(x, "vor", -999))
    return {"players": [player_json(x) for x in lst[:limit]]}


@app.get("/api/flags")
def flags():
    """Everyone the feeds say is hurt, suspended or otherwise at risk."""
    p, _, _ = get_pool()
    # Severity first, then ADP. A preseason "Questionable" tag is mostly noise;
    # a suspension or an IR stint is a draft-altering fact and must not be
    # buried under fifty players with a tweaked hamstring.
    sev = {"SUSPENDED": 0, "IR": 1, "OUT": 1, "PUP": 2, "NFI": 2,
           "Doubtful": 3, "Questionable": 4}
    out = [player_json(x) for x in p
           if (x.flag or x.games_missed) and x.adp < 900]
    out.sort(key=lambda r: (0 if r["games_missed"] else 1,
                            sev.get(r["flag"], 5), r["adp"]))
    return {"flagged": out}


@app.post("/api/refresh")
def refresh(req: RefreshReq):
    t0 = time.time()
    errors = {}
    for name, fn in sources.REFRESHERS.items():
        try:
            fn(force=req.force)
        except Exception as e:
            errors[name] = str(e)
    get_pool(force=True)
    return {"ok": not errors, "errors": errors,
            "seconds": round(time.time() - t0, 1),
            "built_at": _state["built_at"]}


@app.post("/api/recommend")
def api_recommend(req: RecommendReq):
    p, repl, waiver = get_pool()
    res = recommend(p, req.my_slot, req.taken, repl, waiver,
                    my_roster_names=req.my_roster, pick_no=req.pick_no,
                    n_sims=req.n_sims, top_k=req.top_k)
    payload = {"recommendations": res, "pick_no": req.pick_no or len(req.taken) + 1}
    if req.save:
        payload["run_id"] = store.save_run("recommend", req.model_dump(), payload)
    return payload


@app.post("/api/simulate-draft")
def api_sim_draft(req: DraftSimReq):
    p, repl, waiver = get_pool()
    res = simulate(p, req.my_slot, n_sims=req.n_sims, seed=req.seed,
                   repl_pts=repl, waiver=waiver)
    if req.save:
        res["run_id"] = store.save_run("draft", req.model_dump(), res, req.label)
    return res


@app.post("/api/simulate-season")
def api_sim_season(req: SeasonSimReq):
    p, repl, waiver = get_pool()
    mine = None
    if req.my_roster:
        want = [norm_name(n) for n in req.my_roster]
        by = {norm_name(x.name): x for x in p}
        mine = [by[n] for n in want if n in by]
        missing = [n for n, w in zip(req.my_roster, want) if w not in by]
        if missing:
            raise HTTPException(400, f"unknown players: {missing}")
    rosters = build_league(p, req.my_slot, my_players=mine, seed=req.seed,
                           repl_pts=repl, waiver=waiver)
    res = simulate_season(rosters, req.my_slot, n_sims=req.n_sims, seed=req.seed)
    res["my_lineup"] = [
        (x.name, x.pos, round(x.proj, 1)) for x in optimal_lineup(rosters[req.my_slot])[1]]
    if req.save:
        res["run_id"] = store.save_run("season", req.model_dump(), res, req.label)
    return res


@app.get("/api/availability")
def api_availability(my_slot: int, pick_no: int, n_sims: int = 200):
    p, _, _ = get_pool()
    return {"pick_no": pick_no,
            "players": availability_at_pick(p, my_slot, pick_no, n_sims=n_sims)}


@app.get("/api/my-picks")
def my_picks(my_slot: int):
    return {"picks": snake_pick_numbers(my_slot)}


@app.get("/api/runs")
def api_runs(kind: str | None = None):
    return {"runs": store.list_runs(kind)}


@app.get("/api/runs/{run_id}")
def api_run(run_id: int):
    r = store.get_run(run_id)
    if not r:
        raise HTTPException(404, "not found")
    return r


@app.delete("/api/runs/{run_id}")
def api_delete_run(run_id: int):
    store.delete_run(run_id)
    return {"ok": True}


@app.get("/api/overrides")
def api_get_overrides():
    return {"overrides": [{"name": k, "games_missed": v[0], "note": v[1]}
                          for k, v in store.get_overrides().items()]}


@app.post("/api/overrides")
def api_set_override(req: OverrideReq):
    if req.games_missed <= 0:
        store.clear_override(req.name)
    else:
        store.set_override(req.name, req.games_missed, req.note)
    get_pool(force=False)
    with _lock:
        _state["pool"] = None          # force rebuild so the discount applies
    get_pool()
    return {"ok": True}


@app.get("/api/state")
def api_get_state():
    return store.load_draft_state()


@app.post("/api/state")
def api_set_state(req: StateReq):
    store.save_draft_state(req.model_dump())
    if req.my_slot:
        store.set_setting("my_draft_slot", req.my_slot)
    return {"ok": True}


@app.get("/api/strategies")
def api_strategies(my_slot: int = 1):
    """The catalogue plus each plan mapped onto this slot's real pick numbers."""
    return {"strategies": [
        {"key": s.key, "name": s.name, "blurb": s.blurb, "rationale": s.rationale,
         "plan": round_plan(s, my_slot)} for s in STRATEGIES]}


@app.post("/api/backtest-strategies")
def api_backtest(req: StrategyReq):
    p, repl, waiver = get_pool()
    if req.mode == "points":
        res = {"mode": "points",
               "results": backtest(p, req.my_slot, repl, waiver, n_sims=req.n_sims)}
    else:
        res = {"mode": "season",
               "results": backtest_seasons(p, req.my_slot, repl, waiver,
                                           n_drafts=req.n_drafts,
                                           n_seasons=req.n_seasons)}
    res["my_slot"] = req.my_slot
    if req.save:
        res["run_id"] = store.save_run("strategy", req.model_dump(), res, req.label)
    return res


@app.post("/api/advise")
def api_advise(req: AdviseReq):
    p, repl, waiver = get_pool()
    res = advise(p, req.my_slot, req.taken, req.my_roster, repl, waiver,
                 pick_no=req.pick_no, n_sims=req.n_sims,
                 scan_sims=req.scan_sims, top_k=req.top_k)
    if req.save:
        res["run_id"] = store.save_run("advise", req.model_dump(), res)
    return res


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
