# MantisGrid AI API — what we used, what helped, what went wrong

Feedback from Team 16 (Catherine Awad, Shruti Doshi), MantisGrid Hackathon 2026,
Track 2. Prepared for the MantisGrid team after the 5 October meeting.

Everything below was measured against the hackathon facsimile of the API
(`track-2/api/`, checked by the organisers against the product on 2026-09-10) and
the MIT SuperCloud sample it serves. Every claim names the script that reproduces
it; all of them run from `track-2/` with the data generated and the API up.

- **Inventory:** 12 `/v1` endpoints plus `/health`, and an MCP server with 13 tools.
- **Used in the product we shipped:** 9 of the 12 endpoints, and 9 of the 13 MCP tools.
- **Genuine bugs:** 3 (`neighbor`, `queue/latency`, `causal`).
- **Design problems:** 3 (`recommendations`, `resources/underperforming`, `scaling/efficiency`).
- **The findings API:** no bug. What tripped us up was its data, and one of our own
  criticisms of it was wrong; both are set out in section 4.

---

## 1. Inventory

### Layer A — the endpoints that mirror the product

| Endpoint | Purpose | Used | Verdict |
|---|---|---|---|
| `POST /v1/causal` | Resolve a cluster of findings to the resource underneath | **Yes — live on the dashboard** | The most valuable endpoint. Two bugs in its response text. |
| `POST /v1/events/findings` | The corpus of 11,979 findings, with filters and paging | **Yes** | No bug. Its data is easy to misuse (section 4). |
| `GET /v1/policies/rules` | The 24 rules and their status, including the ones that never fired | **Yes** | No problems. A `CLEAR` rule turned out to be evidence. |
| `POST /v1/neighbor` | The resource graph, N hops out | Reviewed only | **Bug:** silent truncation, broken at 2 hops. |
| `POST /v1/detect/{id}` | Per-detector finding counts | Reviewed only | No problems; a health check. |

### Layer B — the proposed business layer

| Endpoint | Purpose | Used | Verdict |
|---|---|---|---|
| `GET /v1/price-book` | $/GPU-hour, $/engineer-hour, date offset | **Yes** | No problems. The override and `+custom` tagging are well designed. |
| `GET /v1/efficiency/summary` | Allocated → computed → completed | **Yes** | Correct; we reproduced it to the hour. |
| `GET /v1/waste/breakdown` | GPU-hours by job outcome | **Yes** | Correct; rightly refuses to total its rows. |
| `GET /v1/queue/latency` | Wait percentiles, monetised | **Yes** (percentiles) | **Bug:** a judgment labelled `kind: "fact"`. |
| `GET /v1/resources/underperforming` | Ranked nodes or users | **Yes** (to rebut it) | Design problem: ranks by finding count without causes. |
| `GET /v1/recommendations` | Two cost cuts with savings | **Yes** (to rebut it) | Design problem: the weakest endpoint. |
| `GET /v1/scaling/efficiency` | Utilization by job width | Reviewed only | Design problem: row-weighted. |

### The MCP server (`mcp_layer/`, 13 tools)

Driven over stdio by `analysis/mcp_demo.py` (`make mcp-demo`); transcript in
`analysis/out_mcp_transcript.md`. **Exercised:** `health`, `list_findings`,
`causal`, `list_rules`, `efficiency_summary`, `waste_breakdown`, `queue_latency`,
`underperforming`, `recommendations`. **Not exercised:** `neighbor`, `detect`,
`price_book`, `scaling_efficiency`.

**Parameters we never exercised:** the `category` filter on findings; `hop_count`
above 1 outside the review; `entity_type=user` on the dashboard (we kept it off the
page on purpose: it ranks people).

---

## 2. What helped most, in order

**1. `POST /v1/causal`.** It is the reason our "what not to do" tile exists. The 121
storage findings resolve to one volume (`pvc/scratch-lustre-02`, score 0.88, nodes
0.31). An array's task failures resolve to the array at score 1.0 with each machine
at its thin share (0.05–0.4), and that spread is itself the evidence the machines
are innocent. We call it live on the dashboard, beside the recommendation it
contradicts. An empty answer for a finding with no `rootCauses` is correct
behaviour and the message says why.

**2. The price-book override.** `?usd_per_gpu_hour=N` on the monetising endpoints,
with the response re-tagged `2026-Q3+custom`, is a good design: a price change is
visibly not an infrastructure change. We built the dashboard's price control on it,
so a CFO re-pricing the page gets the API's own answer, not browser arithmetic.

**3. `GET /v1/efficiency/summary`.** Allocated 594,004, computed 228,904, computed
and completed 100,789: all three reproduced from `jobs.parquet` to the hour. Its
caveat that SM utilisation is a proxy is the honest part, and we carried it through.

**4. `GET /v1/policies/rules` reporting `CLEAR`.** `rules::gpu-pcie-saturated` is
armed and never fired: peak PCIe is 4,240 MB/s of a 15,754 MB/s link. That one
negative result kept "fix the data pipeline" off our recommendations. An endpoint
that hid rules with zero findings would have cost us that.

**5. The findings' `metadata` join keys and `rootCauses`.** `metadata.job_id` and
`metadata.node` let us go from a dollar figure to real job rows without parsing
prose, and `rootCauses` is what makes causal resolution possible at all.

**6. The MCP tool docstrings.** Teaching the agent the `fact` / `judgment`
distinction and "validate a judgment against `causal` before acting" is the right
rule, and it is the rule the `recommendations` endpoint itself does not follow.
When our MCP demo followed the docstrings it reached the opposite conclusion from
the recommendation in two calls.

---

## 3. The bugs

### 3.1 `POST /v1/neighbor` truncates silently and breaks at two hops

- **Where:** `api/main.py:142` — `seen = set(list(seen)[:500])  # keep responses answerable`.
- **What happens:** the node set is cut to 500 with nothing in the response saying
  so. The volume has 2,537 `MOUNTS` edges, so even one hop returns a fifth of its
  neighbours. The cap is applied before edges are collected, so `hop_count: 2`
  returns **500 nodes and 5 edges**, fewer edges than one hop (611), and a graph
  whose nodes are mostly unconnected.
- **Why it matters:** a caller sizing an incident's blast radius gets a wrong
  answer and no warning. We found the 159-vs-121 machine gap in the storage
  incident only because we walked `edges.parquet` directly instead.
- **Reproduce:** `POST /v1/neighbor {"resource_ids": ["<the pvc id>"], "hop_count": 1}` then `2`.
- **Suggested fix:** return `truncated: true` and the real neighbour count; apply
  the cap to a connected subgraph rather than a set; include edge direction and
  per-type counts.
- **What we did instead:** we stopped relying on the endpoint for anything that
  needed the whole graph and walked the organisers' own edge table,
  `data/synthetic/edges.parquet`, directly in pandas (`analysis/case4_storage.py`).
  We took every `MOUNTS` edge into the volume (2,537 pods), joined each pod to its
  machine through the `RUNS_ON` edges, and filtered by the incident window using job
  start and end times. That full, untruncated walk is what showed **159 machines**
  running pods on the volume during the episode, against the **121** that carry a
  finding — a gap the truncated endpoint could not have revealed. We still called
  `/v1/neighbor` in the review, but only at one hop and only to document the
  truncation, never to size the incident.

### 3.2 `GET /v1/queue/latency` labels a judgment as a fact

- **Where:** `api/main.py:484` (`kind="fact"`), with `api/data_loader.py` summing
  `wait_sec` over every job.
- **What happens:** it returns `monetized.amount = 9,330,307` — 98,214 wait-hours
  at $95 — tagged `fact`. That is 6.3× the entire $1.49M GPU bill.
- **Why it is wrong:** the percentiles are facts; the multiplication is a judgment
  that every waiting hour is paid idle time. The rule catalogue's own
  `queue-starvation` text says the opposite: *"elapsed wall-clock… NOT staffed time —
  nobody sits watching a scheduler."* Two measurement choices inflate it further:
  `wait_sec` runs from submission rather than eligibility (22,880 h, 23%, is time
  jobs were held by their own dependencies — all array tasks), and every array task
  counts as a person (53% of jobs are array tasks). Merged per person it is 4,997 h;
  past a 4-hour target, 3,719 h.
- **Suggested fix:** measure from `time_eligible`, group by person, and mark the
  monetised field `kind: "judgment"` with the blocking assumption in `provenance`.
- **What we did instead:** we kept the endpoint's percentiles and rebuilt the total
  ourselves from `jobs.parquet` (`analysis/case8_queue.py`):
  1. **Measured from eligibility, not submission:** queue time = `time_start −
     time_eligible`, which removes the 22,880 h of jobs held by their own
     dependencies and leaves 75,334 h.
  2. **Counted people, not jobs:** for each owner we merged their overlapping waits
     into one interval set, so someone waiting on 1,000 array tasks at once waits
     once. That gives **4,997 person-hours**.
  3. **Counted only the painful part:** time past a 4-hour target, merged per
     person: **3,719 person-hours**.
  4. **Reported hours first and labelled the dollars as an upper bound.** On the
     dashboard the figure reads "worth up to $X if that waiting fully blocks the
     researcher", and it is never added to the GPU savings, which are a different
     budget.

  Going further than the endpoint can, we replayed all 74,838 jobs through our own
  discrete-event simulator (`analysis/sched_sim.py`) with each researcher's
  concurrency quota modelled. That reproduced 40% of the observed waiting (versus 5%
  without quotas) and showed the wait is caused by per-user caps, not capacity: in
  90% of long waits the person was already at their cap while a median 287 of 450
  GPUs sat free.

### 3.3 `POST /v1/causal` quotes inconsistent figures and a hard-coded one

- **Where:** `api/main.py:356` and `:366`.
- **Inconsistent:** the storage incident's sentence is built from the *requested*
  finding's own `fs_latency_p99_ratio`. Per-node ratios run 28.3× to 45.5×, so the
  same incident reads "rose 28.3x" or "rose 45.5x" depending on which of the 121
  findings you ask about. A volume-level answer should quote one volume-level number.
- **Hard-coded:** the second chain hop, `gpu_sm_utilization abrupt_drop -91.4%`
  (`z_score=-7.8`), is a constant. The real telemetry on those 121 machines shows
  31.4% utilisation during the episode against 36.4% the week before — a five-point
  dip. (The incident is the corpus's one synthetic scenario, so the jobs underneath
  carried on; a hop that claims to be measured should be measured or dropped.)
- **Also:** the culprit list always shows the first four nodes at 0.31, which is a
  display choice rather than evidence about those four.
- **What we did instead:** we used `causal` for what it computes — *which*
  resource a cluster of findings resolves to, and the spread of scores across the
  machines — and checked every number in its sentence against the data before
  repeating it (`analysis/case4_storage.py`):
  - **One episode window, rebuilt ourselves.** The findings carry no window, so we
    tested three candidate 48-hour windows against the per-node `impact_gpu_hours`
    using real placements in `gpus.parquet`. The 48 hours *before* detection
    reproduce every node's figure exactly (correlation 1.000), which dates the
    episode and gives one consistent basis instead of a per-finding ratio.
  - **The utilisation hop replaced with measured telemetry.** We compared those 121
    machines' hour-weighted utilisation during the episode (31.4%) with the week
    before (36.4%), the week after (30.6%), and the rest of the cluster in the same
    window (44.9%). We report a mild dip, not the hard-coded −91.4%.
  - **On the dashboard** we show `causal`'s ranked culprits and scores live, because
    those are what the endpoint resolves, and quote its sentence verbatim only
    alongside our own checked figures.
  - **For the array case, which is computed rather than fixed,** we relied on
    `causal` directly: it is the evidence that the API's "drain the top 5 nodes"
    recommendation points at the wrong resources.

---

## 4. The findings API: no bug, but three traps — and our own mistake

`POST /v1/events/findings` behaved correctly throughout. The request takes
`detector_id`, `category`, `severity`, `resource_id`, `limit` (default 100) and
`offset`; the response carries `total`. Nothing we sent it came back wrong.

**Our mistake, for the record.** Our written review (`API_ANALYSIS.md`, now
corrected) said the endpoint "is capped at 100 results" and should "return a total
count and a cursor". It already does both: the 100 was the default `limit`, and
`total` was in every response. Our review script measured `len(findings)` instead
of reading `total`, and also passed an `is_active` filter the request model does
not have, so that call silently returned an unfiltered page. The 73% figure below is
still right because it was computed from `findings.json` directly.

What did cost us time is how easily the *data* is misread:

**Severity is not cost, by 39×.** Job-scope findings: the 31 `CRITICAL` ones carry
7,772 GPU-h ($19K); the 3,215 `HIGH` carry 226,110 ($565K); the 8,254 `LOW` carry
**303,782 GPU-h ($759K)**. `CRITICAL` is reserved for the scheduler's 31 node
failures plus the one hardware fault, so it tracks availability, not money. Sorting
findings by severity and calling it a cost ranking is one query away, and it would
point a CFO at $19K while $759K sits in the rows below.

**`impact_gpu_hours` cannot be summed.** Every finding's impact totals 931,607
GPU-h — **157% of the cluster**. Job-scope only: 537,711 (91%). Deduplicated to the
jobs' real hours: 311,373 (52%). 18% of flagged jobs carry more than one rule, and
`impact_scope` mixes denominators. Two rules also claim a job's full hours whether
or not the GPU was busy: `gpu-memory-oversized` claims 107,009 GPU-h on jobs that
were computing at 49% (low GPU *memory* use is not idle *compute*), and
`wallclock-kill` counts every timed-out hour as lost, including 40,710 GPU-h at
80–100% utilisation.

**`isActive` hides three quarters of the money.** 8,691 findings (73%) are
`RESOLVED`; the active 3,288 are the right default for on-call and the wrong one
for a quarterly cost review.

**What would help:** let a caller order by `metadata.impact_gpu_hours`; return each
finding's share of its job's *real* hours (so a sum cannot exceed the job); and
expose `impact_kind` and `impact_scope` as filters so a client cannot add across
them by accident.

---

## 5. Design problems in Layer B

### 5.1 `GET /v1/recommendations` — do not act on either recommendation

**`rec_drain_nodes` — "Drain the top 5 underperforming nodes", $57,226, effort `low`.**
- Machines are ranked by how many findings touch them (`api/main.py:558`), and
  `rootCauses` is never read.
- The saving sums the five machines' job-scope `impact_gpu_hours` across
  `impact_kind` — `lost` + `consumed` + `unused_capacity` — which `docs/rules.md`
  says produces a number that means nothing.
- Three machines tie at 146 findings for fifth place; dict order picks one.
- The cited `finding_ids` are the first 20 findings on those machines
  (`api/main.py:560`). Passing all 20 to `causal`: **15 have no root cause at all**.
  The array-task findings on the same machines resolve to their arrays at 1.0.
- Draining five 2-GPU machines removes 1,680 GPU-h a week (≈ $55K a quarter) and
  saves nothing: the failing arrays reschedule elsewhere. The one machine in the
  data that really did break (`r216287-n200569`, 140 of 144 jobs failed with a
  SIGBUS signature three unrelated owners never produced elsewhere) ranks 21st by
  finding count.

**`rec_lowutil` — "Move sub-10% utilization workloads to shared allocation", $53,430.**
- It is 35% of `gpu-low-utilization`'s 61,063 GPU-h, and the 0.35 is a constant
  (`api/main.py:567`), not something the data supports.
- It cites 20 of 95 findings (`:570`), and 21 of those 95 jobs use 16+ GPUs, which a
  fractional-GPU queue cannot serve.

**Suggested fix:** route every recommendation through `causal` before publishing it;
express draining as a cost, never a saving; never sum across `impact_kind`; cite all
contributing findings.

**What we did instead.** We replaced both recommendations with three ranked actions
built from `jobs.parquet` and `gpus.parquet`, where every job lands in exactly one
waste class so nothing is counted twice (`analysis/case2_recoverable.py`,
`case2b_cpu_offload.py`, `case3_imbalance.py`):

| Action | Point | Range | How it is counted |
|---|---|---|---|
| End allocations idle at 0% for an hour | $205K | $166K–$212K | Jobs whose *average and peak* were both 0%; hours returned after the grace period. Barely moves between a 1 h and 8 h grace, because these jobs sat idle for days. |
| Move CPU work off GPU machines | $79K | $31K–$83K | Jobs that never opened the GPU at all (0% peak, zero GPU memory, idle power draw). The low end is only those that *completed*, which proves the work did not need a GPU. |
| Right-size 2-GPU jobs using one card | $36K | $29K–$48K | From `gpus.parquet` at one row per card: a card under 5% while its sibling worked. Invisible in `jobs.parquet`, which averages the cards. |

Instead of `rec_lowutil`'s constant 0.35, we split low-utilisation jobs by what the
data actually shows: jobs that *never* computed are counted in full, and jobs that
average under 5% but peak above 50% — 2,564 of them, 43,217 GPU-h of real bursty
work — are counted at half and flagged as the risky part. Each action carries an
owner, a confidence, a range, a "what if we are wrong" note, and a click-through to
the actual job rows and finding IDs.

Instead of `rec_drain_nodes`, we drain **one** machine. We found it with a test that
follows the machine rather than the finding count (`analysis/case5_hardware.py`):
for every (machine, exit status) pair, count owners who crashed there repeatedly
with that status and never produced it anywhere else. Exactly one pair passes:
`r216287-n200569`, SIGBUS, three unrelated owners. On the dashboard, the drain
recommendation appears as a mistake with its real cost ($55K a quarter of
capacity), beside the live `causal` answer that refutes it.

### 5.2 `GET /v1/resources/underperforming` — a finding count is not a drain list

Nodes are ranked by finding count (`api/main.py:529`); the response's own
provenance admits it does not read `rootCauses`. The five it names carry 95–209
array-task findings each — symptoms of other people's scripts. Our like-for-like
triage of all 113 `node-elevated-failure-rate` firings found 46 user code, 40
workload mix, 26 undeterminable and **1** hardware; none of the five is the one.
The user ranking is defensible arithmetic but lists people; we kept it off the page.

**Suggested fix:** collapse findings by `rootCauses` before ranking; rank machines on
evidence that follows the machine (a multi-owner exit-status signature, located node
failures, excess failures after adjusting for each owner's own rate); break ties
explicitly or return all tied machines.

**What we did instead.** We triaged every one of the 113 `node-elevated-failure-rate`
firings ourselves (`analysis/case6_triage.py`). First we reproduced the rule's own
counts — 14-day windows from the first job, jobs counted by `time_end` and placed by
`gpus.parquet` — and 112 of 113 matched exactly. Then each firing got four
like-for-like tests:

- **Hardware:** an exit status two or more owners hit on this machine and on no
  other machine in four months, or two or more node failures located here.
- **User code:** one owner holds 60% or more of the failures, everyone else on the
  machine fails at a normal rate, and the owner's failures follow them — best shown
  by the same Slurm array's tasks failing on other machines.
- **Workload mix:** no dominant owner, and each owner's own failure rate elsewhere
  in the same window explains the count.
- **Cannot determine:** everything else, with the specific reason recorded.

Result: 46 user code, 40 workload mix, 26 cannot determine, **1** hardware. Each
verdict carries its reasoning with the numbers it used, and all 113 are in
`claims.json`. Where the API's own 48-hour burst detector named a cause, we agreed
in 13 of 14. We kept the user ranking off the dashboard entirely: owners stay hashed,
and we present waste as capacity to recover, never as people to blame.

### 5.3 `GET /v1/scaling/efficiency` — right caveat, wrong weighting

Its caveat to read the shares rather than the median is correct. But every figure is
row-weighted, and money is hour-weighted. By GPU-hours, utilisation *rises* with
width to about 16 GPUs (1: 29%, 2: 38%, 3–4: 42%, 5–8: 77%, 9–16: 67%) and falls
back only in the 17–64 band (32%). The 398 jobs of 9+ GPUs hold 16.6% of all
GPU-hours at 59% utilisation; "wide jobs are wasteful" is a job-count artefact.

**Suggested fix:** add a `gpu_hour_weighted_sm_util` column.

**What we did instead.** We recomputed utilisation by width weighted by GPU-hours
from `jobs.parquet` (`analysis/case1_money.py`), and reported both views side by
side so the contradiction is visible: by job count, wide jobs look wasteful (59% of
9+ GPU jobs run under 5%); by GPU-hours, they are the most efficient work on the
cluster up to about 16 GPUs. We therefore left width out of our cut list altogether,
and flagged the 17–64 GPU band (32% hour-weighted) as the one worth investigating.
Wide jobs that do idle are caught by the card-imbalance and idle-timeout actions,
which measure the idle cards directly instead of inferring waste from job size.

---

## 6. Changes we would make, in priority order

1. **Fix `neighbor`'s truncation** (3.1). Silent wrong answers about blast radius are
   the most dangerous thing here.
2. **Relabel `queue/latency`'s monetisation as `judgment`** and measure from
   eligibility, per person (3.2).
3. **Make `recommendations` call `causal`** and stop summing across `impact_kind`
   (5.1). As shipped it would have cost a customer $55K a quarter for nothing.
4. **Quote one volume-level figure in `causal`** and derive or drop the utilisation
   hop (3.3).
5. **Rank machines on cause, not count,** in `underperforming` (5.2).
6. **Add hour-weighted utilisation** to `scaling/efficiency` (5.3), and each
   outcome's *busy* hours to `waste/breakdown` — that one column would have settled
   our longest argument (whether cancelled jobs are waste) inside the response.
7. **On findings:** orderable by impact, impact as a share of the job's real
   hours, `impact_kind`/`impact_scope` as filters (4).

---

## 7. Reproducing everything

```bash
cd track-2                     # data generated per data/README.md, API up on :8000
python3 analysis/api_review.py # every endpoint checked against the tables -> out_api_review.json
make mcp-demo                  # drives the MCP server -> analysis/out_mcp_transcript.md
python3 analysis/case4_storage.py    # the storage incident, and the causal hop vs real telemetry
python3 analysis/case6_triage.py     # all 113 node-elevated-failure-rate verdicts
python3 analysis/case7_drain_rec.py  # the drain recommendation, line by line
python3 analysis/case8_queue.py      # the queue figures, per person
```

The fuller write-ups are `API_ANALYSIS.md` (endpoint by endpoint) and
`ANALYSIS.md` (the data work, cases 1–10). Numbers were measured on the corpus that
`make check-data` verifies, so they should match yours exactly.
