# The Petting Zoo — Draft Assistant

A Monte Carlo draft and season simulator built for one specific ESPN league
(id `20543034`): 10 teams, full PPR, 14-man rosters, snake draft.

## Run it

```bash
python3 run.py
```

Opens <http://127.0.0.1:8077>. First launch downloads ~25MB of data and takes
about a minute; after that it starts instantly from cache.

## What each tab does

| Tab | Purpose |
|---|---|
| **Draft Board** | Draft-day command centre. Mark players as they go, hit *Run Monte Carlo recommendation* on your turn. ~6s per run. |
| **Players** | Full pool ranked by value over replacement, with source disagreement and measured weekly volatility. |
| **Injuries & Suspensions** | Everyone the feeds flag, worst first. Set games-missed manually to discount a projection everywhere. |
| **Draft Simulator** | Plays your whole draft hundreds of times; shows what you realistically end up with at each pick. |
| **Season Simulator** | Your roster vs the other nine, 14 weeks + playoffs. Playoff and title odds. |
| **Saved Runs** | Everything is stored in SQLite and reloadable. |

## Data sources — all free, none need a key

| Source | Provides | Refresh |
|---|---|---|
| ESPN fantasy API | 2026 projections, 2025 actuals, ESPN ADP, auction values, injury status | live |
| Sleeper | injury/suspension status, depth charts, cross-platform IDs | ~6h cache |
| Fantasy Football Calculator | mock-draft ADP **with per-player standard deviation** | ~12h cache |
| nflverse | 2020–2025 game logs, 2026 schedule, Vegas lines | daily |
| DynastyProcess | player ID crosswalk | daily |

### Data freshness

Nothing streams live. Each source has a time-to-live and is re-pulled on the
next load once it expires — no action needed from you.

| Source | Refreshes after | Why |
|---|---|---|
| ESPN (ADP, injuries, projections) | 3 h | The only genuinely live inputs |
| FFC ADP | 12 h | FFC recomputes once a day |
| Sleeper players | 12 h | 15MB; Sleeper asks for ≤1 pull/day |
| Sleeper projections | 24 h | Move slowly |
| Schedule, ID crosswalk | 7 d | Effectively static |
| Historical game logs | never | Past seasons do not change |

Two caches sit in front of these, and both have a 30-minute TTL so the
per-source limits above actually get consulted: `@st.cache_resource` in the
Streamlit app and the in-process pool in the FastAPI app. Without those TTLs a
long-running app would serve its very first snapshot forever.

Hit **↻ Refresh all data** to force everything immediately — worth doing right
before the draft starts, and after any injury news breaks.

## How the modelling works

**Scoring** is exact. The league's rules are transcribed in `league.py` and
verified to reproduce ESPN's own applied totals for 459 of 460 players to
within 0.01 points. For QB/RB/WR/TE this league is precisely ESPN standard full
PPR; only K and D/ST differ.

**Variance is measured, not assumed.** `variance.py` reads four seasons of real
game logs, scores every player-week under this league's rules, and fits how
weekly standard deviation scales with weekly output, per position. It also
measures availability without the survivorship bias that comes from selecting
on games played. Result: a starter averages ~14 games, and fewer than half play
16 or more.

**Opponents are modelled from real behaviour.** Each simulated pick perturbs
every available player's ADP by that player's own measured standard deviation
and takes the best result that fits the team's roster needs.

**Picks are valued by marginal lineup gain, not raw points.** Raw projections
rank a QB first in a 1-QB league, which is wrong. Value over replacement fixes
that; marginal lineup value fixes the follow-on problem of hoarding a scarce
position you can only start one of. Empty starter slots are charged at the
*waiver* level, because by round 8 the preseason "replacement" player is gone.

**K and D/ST are priced as streaming positions** — their replacement level is
set near the top of the position, because you can pick up a comparable one off
waivers every week.

## Known limits

- The league's real head-to-head schedule is not public, so `season.py`
  generates a faithful approximation (division rivals twice, everyone else
  once, one rotating extra). Swap in the real pairings if you export them.
- Season-sim odds measure your roster against *simulated* opponents who follow
  ADP. Real leaguemates are not uniform ADP followers, so treat the numbers as
  directional rather than literal.
- No public feed reports how many games a suspension will cost. Set it by hand
  on the Injuries tab.
- D/ST projections use ESPN's applied total rather than this league's
  yards-allowed tiers, which are not decodable from ESPN's component stats.

## Layout

```
pettingzoo/
  league.py      league config + scoring (the single source of truth)
  scoring.py     scoring engines for skill / K / D-ST
  sources.py     data fetchers, all cached to data/
  pool.py        merges sources into one player pool
  valuation.py   replacement level, VOR, tiers, waiver levels
  variance.py    empirical weekly variance + availability model
  draft.py       Monte Carlo draft + recommendation engine
  season.py      14-week season, playoffs, title odds
  store.py       SQLite persistence
  api.py         FastAPI backend
  ui.py          shared design tokens + HTML table renderers (Streamlit build)
  web/           single-page frontend (local build)
```

---

## Out-of-the-box strategies

Eight named plans (Balanced, Robust RB, Hero RB, Zero RB, Elite TE, Late-Round
QB, Early Elite QB, WR Heavy) live in `strategies.py`. Each is a round-by-round
position plan, mapped onto your actual pick numbers for your slot.

They are not asserted — they are **backtested**. Every strategy is drafted
against the ADP opponent model hundreds of times and scored two ways:

- **Projected lineup points** (fast). Note this quietly favours the Balanced
  plan, because that is the exact number the Balanced policy maximises. Useful
  for a quick read, not for picking a plan.
- **Title odds** (slower, honest). Each drafted roster plays ~1,600 real
  seasons with weekly variance, injuries and byes. This is the objective that
  actually matters, and it is where a strategy's risk profile shows up.

## Dynamic advisor

Toggle it on in the Draft Board. On every pick it answers three questions at
once:

1. **Which player?** Forward rollouts — each candidate is forced as your pick
   and the rest of the draft played out, so the answer accounts for who will
   still be there at your next turn.
2. **Which position is about to get scarce?** A cost-of-waiting scan plays the
   gap to your next pick many times and measures how far the best player at
   each position decays, plus how often the top name survives.
3. **What is the board doing?** Positional run detection against baseline pick
   shares, so a run on RBs is called out while it is happening.

Position ranking and player choice use the **same** metric (expected finished
roster), so the board and the recommendation can never disagree. Every call
comes with plain-language reasoning rather than a bare name.

## Hosting

Two ways to run it, sharing one engine:

| | Local (FastAPI) | Hosted (Streamlit) |
|---|---|---|
| Start | `python3 run.py` | `streamlit run streamlit_app.py` |
| Look | Custom dark UI | Same design, ported via `ui.py` |
| Speed | Fastest; no reruns | Reruns per interaction |
| Best for | **Draft night** | Phone, tablet, anywhere |

### Deploy to Streamlit Community Cloud (free)

1. Create an empty repo on GitHub.
2. `git remote add origin <your-repo-url> && git push -u origin main`
3. Go to <https://share.streamlit.io>, click **New app**, pick the repo, set
   main file to `streamlit_app.py`, deploy.

`requirements.txt` and `.streamlit/config.toml` are already set up.
`data/variance_model.json` is committed on purpose: it is 1.5KB, but rebuilding
it needs 32MB of historical game logs, so shipping it keeps cold boots fast.

**Caveat worth knowing:** free hosts use ephemeral storage. Saved runs and
manual games-missed overrides live in SQLite under `data/`, so they reset when
the app sleeps or redeploys. Player data re-downloads automatically. For draft
night itself, run locally — no cold starts, no network dependency.
