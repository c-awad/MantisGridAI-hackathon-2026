# MantisGrid Hackathon 2026 — Track 2 submission

**Cluster efficiency: where to cut $1.49M of GPU spend, and what not to touch.**

`docker-compose.yml`, `claims.json` and `REPORT.md` are here at the repository root, as the
brief asks. The project itself lives in [`track-2/`](track-2/).

| | |
|---|---|
| **The dashboard** | `docker compose up` (from the repository root) → **http://localhost:3000** (five tabs; the same data as one long page at `/classic/`) |
| **The report** | [`REPORT.md`](REPORT.md) |
| **The claims** | [`claims.json`](claims.json) |
| **The working** | [`track-2/ANALYSIS.md`](track-2/ANALYSIS.md) — case by case, 1 to 9b |
| **The API review** | [`track-2/API_ANALYSIS.md`](track-2/API_ANALYSIS.md) — every endpoint, and three bugs |
| **Feedback for MantisGrid** | [`track-2/API_FEEDBACK.md`](track-2/API_FEEDBACK.md) — what we used, what helped most, the bugs with line numbers, and what we would change |
| **The scripts** | [`track-2/analysis/`](track-2/analysis/) — one per case, plus the scheduler simulator |

This repository is a fork of the organisers' `hackathon-2026-official`, so the
original briefs and guides are still in place (`track-2/README.md`,
`track-2/docs/`), with our work added alongside them. `track-1/` is untouched.

## Running it

> **⚠️ The dashboard takes about 90 seconds to appear after `docker compose up`.**
> It is not hung. On startup it rebuilds all its numbers from `data/` and runs 13
> scheduler simulations over all 74,838 jobs. Wait for this line in the log, then
> open the page:
>
> ```
> dashboard-1  | dashboard on http://0.0.0.0:3000
> ```

**Step 1 — clone**

```bash
git clone https://github.com/c-awad/MantisGridAI-hackathon-2026.git
cd MantisGridAI-hackathon-2026
```

**Step 2 — generate the data** (about a minute; the licence forbids committing it).
These are the organisers' own steps from `track-2/data/README.md`, unchanged:

```bash
cd track-2
curl -O https://mantisgrid-hackathon.s3.us-east-1.amazonaws.com/track-2-raw.zip
unzip track-2-raw.zip -d data/raw
make prep          # raw CSVs  -> data/prepped/
make generate      # prepped   -> data/synthetic/
make check-data    # should end with: "Your data matches."
cd ..
```

If you already hold the five generated files, put them in `./data/` at the repository
root instead. It is a symlink to `track-2/data/`, so both routes land in the same place.

**Step 3 — start everything, with one command, from the repository root**

```bash
docker compose up
```

**Step 4 — wait about 90 seconds, then open http://localhost:3000**

| What | Where |
|---|---|
| The dashboard (five tabs) | http://localhost:3000 |
| The same data as one long page | http://localhost:3000/classic/ |
| The MantisGrid API and its interactive spec | http://localhost:8000/docs |

**What you will see in the terminal while you wait.** The API prints a `GET /health`
line every 5 seconds forever; that is Docker's health check and is harmless. The
dashboard prints `reading tables`, then `simulating the scheduler...`, then one line
per simulation, and finally `dashboard on http://0.0.0.0:3000`. To watch only the
dashboard: `docker compose logs -f dashboard`.

**In a hurry?** `DASH_SIM=0 docker compose up` skips the simulations and starts in a
few seconds. Tab 4's quota dial is then empty; everything else works.

**If something goes wrong**

- *The dashboard container exits with a file-not-found error:* the data is missing.
  Do step 2, then `docker compose up` again.
- *The page shows old numbers or a mix of old and new:* hard-refresh the browser.
  The server sends `no-store`, so this should not happen after the first load.
- *Port 3000 or 8000 is already in use:* stop whatever holds it, or
  `docker compose down` a previous run first.

**Optional checks**

```bash
cd track-2
make validate URL=http://localhost:3000     # validates ../claims.json and pings the dashboard
make mcp-demo                               # drives the MCP server (needs uv); writes a transcript
python3 analysis/case2_recoverable.py       # reproduce any single case
```

## Who built what

The dashboard's **UI design is Shruti Doshi's** — the tabbed shell, KPI rows, spend
wheel, row components and stylesheet come from her dashboard, preserved in
`track-2/Shruti's dashboard/`. The version served on `:3000` puts that design on top
of the shared data pipeline (`dashboard/build.py`): one waste class per job, live
`POST /v1/causal`, the price-book override through the `/api` proxy, and the
scheduler simulation behind tab 4.

## What we found, in short

- **$359K recoverable** of $1.49M (range $202K–$491K). Most of it is GPUs nobody was
  using: 97,196 GPU-hours never ran a single kernel.
- **One machine to drain**, not the 121 the storage findings suggest and not the 5
  the API recommends. `r216287-n200569` failed 140 of 144 jobs with a SIGBUS
  signature three unrelated owners never produced anywhere else, and the scheduler
  never marked it down.
- **Hardware caused 0.8% of failures** (145 of 18,587). Almost everything else is
  user code.
- **The queue is a quota problem, not a capacity one.** In 90% of waits over an
  hour the researcher was already at their own concurrency cap while a median 287 of
  450 GPUs sat free. An elastic quota plus an idle timeout removes 96% of the
  modelled waiting: **1,267 person-hours** returned to researchers (up to $120K if
  that waiting fully blocks them), at no capacity cost.
- **Three API bugs**, with file and line, in `track-2/API_ANALYSIS.md`.

## AI models, assistants and frameworks we used

**All of the code, analysis and prose in this submission was produced with
[Claude Code](https://claude.com/claude-code) in an interactive session, running
Claude Opus 5 (1M context) for almost all of it and Claude Fable 5.1 for the final
case (case 10, the credits pool).** No other AI model, agent framework or coding assistant
was used, and no LLM is called at runtime by anything we shipped: the dashboard and
every analysis script are plain Python and JavaScript over the prepped Parquet
tables and the MantisGrid API.

Specifically AI-generated: the nine analysis scripts in `analysis/`, the scheduler
simulator, `dashboard/` (build, server, page and charts), `claims.json`, and the
three documents (`REPORT.md`, `ANALYSIS.md`, `API_ANALYSIS.md`).

What the team did: chose the track and the questions to ask, directed each case,
pushed back on numbers that looked wrong, and made the judgement calls the brief
asks teams to defend — whether cancelled jobs count as waste, where to set the
idle-timeout grace period and the elastic-quota threshold, and which findings were
worth arguing with. Several of the strongest results exist because the team asked
for them rather than because the model proposed them: the CPU-offload tiering, the
scheduler simulation and its per-user-quota model, the MCP transcript, the
dashboard's price control, and the credits pool — including the two design changes
that made it work (never reduce the lender's own quota; set the loan window to the
mean job length and gate on predicted runtime).

Corrections the team's questions forced, all recorded in the documents: the
"never ran a kernel" figure was initially overstated ($369K → $243K, because a
zero *average* is not a zero *peak*), the wide-job utilisation claim was not
monotonic once banded finely, and the first scheduler simulation omitted per-user
quotas and so reproduced only 1% of the observed waiting.

The MantisGrid API facsimile, its MCP layer (`track-2/mcp_layer/`), the data
preparation scripts and the findings generator are the **organisers'** code, not
ours.

## Data and licences

The data is not in this repository and must not be: MIT SuperCloud publishes it
under CC BY-NC-ND 4.0, which forbids redistributing anything derived from it. See
[`ATTRIBUTION.md`](ATTRIBUTION.md). Generate `data/` yourself with the steps above;
`make check-data` confirms your five files match the organisers' checksums, which
ours do.

MIT publishes this as a four-month **sample** of the cluster's jobs and notes it is
*"not appropriate for use in estimating system utilization"*. Every figure in this
submission describes that sample, and the documents say so wherever it matters.
