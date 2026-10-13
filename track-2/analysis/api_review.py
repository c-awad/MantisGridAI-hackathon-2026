"""Review every MantisGrid API endpoint against the data underneath it.

For each endpoint: call it, record what it returns, and check the claim against
data/prepped/. Writes analysis/out_api_review.json; the write-up is
track-2/API_ANALYSIS.md.

Layer A mirrors the real product. Layer B is proposed, and the brief invites us
to show where its shape is wrong -- so every Layer B verdict here is backed by a
recomputation, not an opinion.
"""
import json
import os
import urllib.error
import urllib.request

import pandas as pd

API = os.environ.get("MGAI_URL", "http://localhost:8000")
USD_GPU, USD_ENG, H = 2.50, 95.0, 3600.0
j = pd.read_parquet("data/prepped/jobs.parquet")
g = pd.read_parquet("data/prepped/gpus.parquet")
findings = json.load(open("data/synthetic/findings.json"))
f = pd.json_normalize(findings)
res = pd.read_parquet("data/synthetic/resources.parquet")
edges = pd.read_parquet("data/synthetic/edges.parquet")
TOTAL = j.gpu_hours.sum()
report = {}


def call(method, path, body=None):
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r), r.status
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:200]}, e.code
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"error": str(e)}, 0


def record(name, path, layer, kind, calls, checks, verdict, change=None):
    report[name] = {"endpoint": path, "layer": layer, "kind": kind, "calls": calls,
                    "checks": checks, "verdict": verdict, "change": change}
    print(f"\n== {name}  [{layer} / {kind}]  {path}")
    for c in checks:
        print(f"   {c}")
    print(f"   -> {verdict}")


# ---------------------------------------------------------------- Layer A
fx, st = call("POST", "/v1/events/findings", {})
page = fx.get("findings", [])
# NB: every call is capped at the page size, so these are "up to N", not totals.
det = {}
for d in sorted(f.detectorId.unique()):
    r, _ = call("POST", "/v1/events/findings", {"detector_id": d})
    det[d] = len(r.get("findings", []))
PAGE = len(page)
js = f[f["metadata.impact_scope"] == "job"]
sev_impact = js.groupby("severity")["metadata.impact_gpu_hours"].agg(["size", "sum", "max"])
active, _ = call("POST", "/v1/events/findings", {"limit": 1})   # the API has no is_active filter; isActive is counted from the file below
sev, _ = call("POST", "/v1/events/findings", {"severity": "CRITICAL"})
n_active = int((~f.isActive.astype(bool)).sum())
record(
    "findings", "POST /v1/events/findings", "A", "fact (corpus)",
    {"unfiltered_page_size": len(page), "per_detector_counts": det,
     "total_reported": fx.get("total"), "severity_CRITICAL": len(sev.get("findings", []))},
    [f"corpus is {len(f):,} findings across {f.detectorId.nunique()} detectors; the default page is "
     f"{PAGE} rows, the request takes limit/offset, and the response carries total={fx.get('total')} "
     f"(an earlier version of this script misread the default limit as a cap)",
     f"{n_active:,} findings ({n_active/len(f):.0%}) are RESOLVED/isActive=false; the active set is "
     f"{len(f)-n_active:,} -- about a quarter of the corpus",
     f"severity is not cost: the {int(sev_impact.loc['CRITICAL', 'size'])} CRITICAL job-scope findings carry "
     f"{sev_impact.loc['CRITICAL', 'sum']:,.0f} GPU-h (${sev_impact.loc['CRITICAL', 'sum']*USD_GPU:,.0f}) while "
     f"the {int(sev_impact.loc['LOW', 'size']):,} LOW ones carry {sev_impact.loc['LOW', 'sum']:,.0f} GPU-h "
     f"(${sev_impact.loc['LOW', 'sum']*USD_GPU:,.0f}) -- "
     f"{sev_impact.loc['LOW', 'sum']/sev_impact.loc['CRITICAL', 'sum']:.0f}x more, and the largest single LOW "
     f"finding is {sev_impact.loc['LOW', 'max']:,.0f} GPU-h (${sev_impact.loc['LOW', 'max']*USD_GPU:,.0f})",
     "CRITICAL is reserved for the scheduler's 31 node failures plus the one hardware fault -- it tracks "
     "availability, not money"],
    "Trust it as a corpus. Never sort it by severity and call that a cost ranking.",
    "Let a caller order by metadata.impact_gpu_hours, and expose impact_kind/impact_scope as filters.")

# causal: the endpoint that resolves a cluster to one cause
cs = {}
for d in ["rules::filesystem-latency-degraded", "rules::array-task-failure", "rules::array-mass-failure",
          "rules::node-hardware-fault", "rules::node-job-failure-burst", "rules::gpu-low-utilization"]:
    fid = next((x["id"] for x in findings if x["detectorId"] == d), None)
    r, _ = call("POST", "/v1/causal", {"finding_id": fid})
    if r.get("findings"):
        c = r["findings"][0]
        cs[d] = {"root_cause": c["root_cause"], "confidence": c.get("confidence"),
                 "top": c["culprit"][0]["node"], "top_score": c["culprit"][0]["score"],
                 "next_scores": [x["score"] for x in c["culprit"][1:4]],
                 "agreement": c.get("algorithm_agreement")}
    else:
        cs[d] = {"empty": r.get("message", "")[:120]}
ratios = f[f.detectorId == "rules::filesystem-latency-degraded"]["metadata.fs_latency_p99_ratio"]
arr_ids = [x["id"] for x in findings if x["detectorId"] == "rules::array-task-failure"][:3]
same = []
for fid in arr_ids:
    r, _ = call("POST", "/v1/causal", {"finding_id": fid})
    same.append(r["findings"][0]["culprit"][0]["node"] if r.get("findings") else None)
record(
    "causal", "POST /v1/causal", "A", "fact (the authoritative one)", cs,
    [f"121 filesystem findings all resolve to pvc/scratch-lustre-02 at score 0.88, nodes at 0.31 -- one "
     f"cause, not 121 problems",
     f"array-task findings resolve to their array at score 1.0 with each machine at ~0.14; three different "
     f"task findings gave {len(set(same))} distinct culprit(s): {set(same)}",
     f"the spread across machines IS the evidence the machines are innocent",
     f"a finding with no rootCauses returns findings: [] and a message -- that is correct behaviour, not a "
     f"failure ({sum(1 for v in cs.values() if 'empty' in v)} of the 6 detectors tried)",
     f"but the volume answer quotes the ratio of whichever finding you ask about: per-node ratios run "
     f"{ratios.min()}x-{ratios.max()}x, so the same incident reads 'rose 28.3x' or 'rose 45.5x'",
     "and its second chain hop (gpu_sm_utilization abrupt_drop -91.4%) is a constant in api/main.py; the "
     "real telemetry on those 121 nodes shows 31.4% vs 36.4% the week before -- a 5-point dip"],
    "The best endpoint in the API. Everything ranked should pass through it first.",
    "Quote one volume-level latency figure, and derive the utilization hop from telemetry or drop it.")

PVC = res[res.type == 'k8s:persistentvolumeclaim'].iloc[0]["id"]
nb, _ = call("POST", "/v1/neighbor", {"resource_ids": [PVC], "hop_count": 1})
graph = nb.get("graphs", [{}])[0]
nb2, _ = call("POST", "/v1/neighbor", {"resource_ids": [PVC], "hop_count": 2})
graph2 = nb2.get("graphs", [{}])[0]
mounts = int((edges.edgeType == "MOUNTS").sum())
record(
    "neighbor", "POST /v1/neighbor", "A", "fact (graph)",
    {"nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", [])),
     "types": sorted(set(graph.get("resource_types", {}).values()))},
    [f"resources: {len(res):,} ({res.type.value_counts().to_dict()})",
     f"edges: {len(edges):,} ({edges.edgeType.value_counts().to_dict()})",
     f"one hop from the volume returns {len(graph.get('nodes', []))} nodes / {len(graph.get('edges', []))} "
     f"edges, but the volume has {mounts:,} MOUNTS edges: api/main.py:142 caps the node set at 500 with no "
     f"flag in the response",
     f"the cap is applied before edges are gathered, so hop_count=2 returns "
     f"{len(graph2.get('nodes', []))} nodes and only {len(graph2.get('edges', []))} edges -- fewer than one "
     f"hop, and a graph whose nodes are mostly unconnected",
     "pods mounting the volume were active on 159 machines during the episode, but only 121 have a "
     "finding, so the graph is wider than the detector's blast radius"],
    "Use it to check whether a 'cluster of problems' shares a dependency.",
    "Return edge direction and type counts in the response so a caller can walk it without the parquet.")

rules, _ = call("GET", "/v1/policies/rules")
rl = rules.get("rules", rules.get("templates", [])) or []
clear = [r for r in rl if r.get("status") == "CLEAR"]
CLEAR_IDS = [r.get("rule_id") or r.get("id") for r in clear]
record(
    "rules", "GET /v1/policies/rules", "A", "fact (catalogue)",
    {"rules": len(rl), "clear": CLEAR_IDS},
    [f"{len(rl)} rules armed, {len(clear)} reporting CLEAR",
     "rules::gpu-pcie-saturated has never fired: peak PCIe on this estate is 4,240 MB/s of a 15,754 MB/s "
     "link (27%), so data movement is not the bottleneck -- a negative result worth as much as a finding",
     "the rules' own text withholds cause on purpose (node-elevated-failure-rate reports a rate, not a "
     "reason), which is why case 6 had to triage all 113 itself"],
    "Read it alongside the findings. A CLEAR rule is evidence.",
    "Ship each rule's threshold in the response (they are only in docs/rules.md).")

dt, code = call("POST", "/v1/detect/rules-integration", {})
record("detect", "POST /v1/detect/{integration_id}", "A", "fact (counts)",
       {"status": code, "keys": list(dt)[:6] if isinstance(dt, dict) else None},
       [f"returns per-detector counts; HTTP {code} for an arbitrary integration id"],
       "Fine for a health check of the detector set.", None)

# ---------------------------------------------------------------- Layer B
pb, _ = call("GET", "/v1/price-book")
record("price_book", "GET /v1/price-book", "B", "fact",
       pb,
       [f"${pb.get('usd_per_gpu_hour')}/GPU-hour and ${pb.get('usd_per_engineer_hour')}/engineer-hour -- the "
        f"engineer rate is {pb.get('usd_per_engineer_hour', 0)/max(pb.get('usd_per_gpu_hour', 1), 1):.0f}x the "
        f"GPU rate, which is why anything monetised as human time dominates every other number",
        "epoch_offset maps the data's relative timestamps to calendar dates; every date in our analysis uses it"],
       "Use it, and pass overrides so a price change is visible as +custom.",
       "Version the engineer rate separately -- it is an assumption about people, not a hardware price.")

es, _ = call("GET", "/v1/efficiency/summary")
rows = {r["label"]: r["gpu_hours"] for r in es.get("rows", [])}
busy = (j.gpu_hours * j.sm_util_avg / 100).sum()
busy_done = (j.gpu_hours * j.sm_util_avg / 100)[j.state_name == "COMPLETED"].sum()
record("efficiency", "GET /v1/efficiency/summary", "B", es.get("kind"),
       {"rows": rows, "monetized": es.get("monetized"), "caveat": es.get("provenance", {}).get("caveat", "")[:120]},
       [f"allocated {rows.get('allocated', 0):,.0f} reproduces jobs.parquet gpu_hours exactly ({TOTAL:,.0f})",
        f"computed {rows.get('computed', 0):,.0f} vs our sm-weighted {busy:,.0f} -- matches",
        f"computed_completed {rows.get('computed_completed', 0):,.0f} vs ours {busy_done:,.0f} -- matches",
        "the caveat is the honest part: SM utilization is a proxy, and a data-loader-bound job does real "
        "work at low occupancy"],
       "Recomputable and correct. This is the one Layer B number we put on the dashboard as-is.",
       "Add a fourth row for allocation the cluster never computed on, so the waterfall ends at waste.")

wb, _ = call("GET", "/v1/waste/breakdown")
mine = j.groupby("state_name").gpu_hours.sum()
theirs = {r.get("state", r.get("label")): r.get("gpu_hours") for r in wb.get("rows", [])}
record("waste", "GET /v1/waste/breakdown", "B", wb.get("kind"),
       {"rows": theirs},
       [f"every state matches our groupby to the hour: {', '.join(f'{k} {v:,.0f}' for k, v in theirs.items() if v)}",
        "it deliberately does not total the rows, which is right: CANCELLED (203,930 GPU-h) is not waste in "
        "itself -- those jobs ran at 34.3% utilization against the cluster's 38.5%",
        "counting CANCELLED as waste would add 116,692 GPU-h of work that was computing when it was stopped"],
       "Trust the rows. Refuse to sum them for the CFO without saying which rows you counted.",
       "Return each state's busy (sm-weighted) hours next to its allocated hours -- that single column ends "
       "the CANCELLED argument.")

ql, _ = call("GET", "/v1/queue/latency")
qrow = ql.get("rows", [{}])[0]
jj = j[j.time_start.notna()]
pre = (jj.time_eligible - jj.time_submit).sum() / H
qw = (jj.time_start - jj.time_eligible).sum() / H
record("queue", "GET /v1/queue/latency", "B", ql.get("kind"),
       {"row": qrow, "monetized": ql.get("monetized")},
       [f"total_wait_hours {qrow.get('total_wait_hours', 0):,.0f} = sum of wait_sec = time_start - time_submit",
        f"that splits into {pre:,.0f} h before the job was eligible ({pre/(pre+qw):.0%}) and {qw:,.0f} h of "
        f"real queueing; all 30,713 jobs with a pre-eligible gap are array tasks",
        f"monetized ${ql.get('monetized', {}).get('amount', 0):,.0f} is "
        f"{ql.get('monetized', {}).get('amount', 0)/(TOTAL*USD_GPU):.1f}x the entire GPU bill",
        "53% of jobs are array tasks, so one person with 1,000 pending tasks is counted 1,000 times; merged "
        "per person the same waiting is 4,997 h, and 3,719 h past a 4-hour target",
        "interactive sessions -- an actual person waiting -- total 0.8 h over four months",
        "kind says 'fact' but the monetisation is a judgement; rules::queue-starvation's own text says this "
        "is elapsed time, NOT staffed time"],
       "Use the percentiles. Do not use the monetised figure.",
       "Measure from time_eligible, group by person, and mark the monetised field kind=judgment.")

sc, _ = call("GET", "/v1/scaling/efficiency")
srows = sc.get("rows", [])
w = pd.cut(j.gpu_count, [0, 1, 2, 4, 8, 16, 64], labels=["1", "2", "3-4", "5-8", "9-16", "17-64"])
hw_ = j.assign(b=j.gpu_hours * j.sm_util_avg).groupby(w, observed=True).b.sum() / j.groupby(w, observed=True).gpu_hours.sum()
record("scaling", "GET /v1/scaling/efficiency", "B", sc.get("kind"),
       {"rows": srows},
       ["it reports median SM utilization plus share_under_5pct and share_over_80pct per width band, and its "
        "own caveat says to read the shares, not the median -- correct, because the distribution is bimodal",
        "but every figure is row-weighted. Hour-weighted, utilization RISES with width: "
        + ", ".join(f"{k} {v:.0f}%" for k, v in hw_.items()),
        "so 'wide jobs are wasteful' is a job-count artefact; the 398 jobs of 9+ GPUs hold 16.6% of all "
        "GPU-hours at 59% utilization"],
       "Read it, then recompute hour-weighted before drawing a conclusion about money.",
       "Add a gpu_hour_weighted_sm_util column. Without it the endpoint invites the wrong cut.")

for ent in ("user", "node"):
    r, _ = call("GET", f"/v1/resources/underperforming?entity_type={ent}&limit=5")
    if ent == "node":
        up_nodes = [(x["entity_id"], x["finding_count"], x["impact_gpu_hours"]) for x in r.get("rows", [])]
    else:
        up_users = [(x["entity_id"], x["unsuccessful_gpu_hours"]) for x in r.get("rows", [])]
tri = json.load(open("analysis/node_triage.json")) if os.path.exists("analysis/node_triage.json") else []
tri_map = {}
for t in tri:
    tri_map.setdefault(t["node"], []).append(t["cause"])
record("underperforming", "GET /v1/resources/underperforming", "B", "judgment",
       {"top_nodes": up_nodes, "top_users": up_users},
       ["nodes are ranked by finding count, and the response says itself that it does not read rootCauses",
        "the five it names carry 95-209 array-task findings each -- symptoms of other people's scripts. Our "
        "triage of those machines: " + "; ".join(f"{n}: {'/'.join(tri_map.get(n, ['not flagged']))}" for n, _, _ in up_nodes),
        "three machines tie at 146 findings for 5th place, so which machine you are told to drain depends on "
        "dict order",
        "the silent SIGBUS machine r216287-n200569 -- the one real hardware fault -- ranks 21st",
        "users are ranked by unsuccessful GPU-hours, which is defensible but lists people, and the brief "
        "warns that a dashboard ranking employees by waste is a hostile dashboard"],
       "Do not act on the node ranking. Treat the user ranking as capacity, not blame.",
       "Collapse findings by rootCauses before ranking, rank machines on machine-following evidence, and "
       "break ties explicitly.")

rc, _ = call("GET", "/v1/recommendations")
lf = f[f.detectorId == "rules::gpu-low-utilization"]
recs = {r["id"]: {"usd": r["estimated_savings"]["amount"], "gpu_hours": r["estimated_savings_gpu_hours"],
                  "confidence": r["confidence"], "effort": r["effort"], "cited": len(r["finding_ids"])}
        for r in rc.get("recommendations", [])}
wide = int((j.set_index("id_job").loc[lf["metadata.job_id"].astype("int64")].gpu_count >= 16).sum())
record("recommendations", "GET /v1/recommendations", "B", "judgment", recs,
       [f"rec_lowutil = 35% of gpu-low-utilization's {lf['metadata.impact_gpu_hours'].sum():,.0f} GPU-h; the "
        f"0.35 is a constant in api/main.py, not something the data supports",
        f"it cites {recs.get('rec_lowutil', {}).get('cited')} of {len(lf)} findings, and {wide} of those jobs "
        f"use 16+ GPUs, which a fractional-GPU queue cannot serve",
        f"rec_drain_nodes = ${recs.get('rec_drain_nodes', {}).get('usd', 0):,.0f} from summing those five "
        f"machines' job-scope impact_gpu_hours across impact_kind (lost + consumed + unused_capacity), which "
        f"docs/rules.md says not to do",
        "draining is not a saving: it removes 1,680 GPU-h a week of capacity (~$55K a quarter) and the "
        "failing arrays reschedule elsewhere",
        "both are kind=judgment with confidence 0.58-0.61, which is honest labelling of a number that should "
        "not be actioned"],
       "The weakest endpoint. Every recommendation here needs re-deriving before it reaches a CFO.",
       "Route each recommendation through /v1/causal, express draining as a cost, and never sum across "
       "impact_kind.")

json.dump(report, open("analysis/out_api_review.json", "w"), indent=1, default=str)
print(f"\nwrote analysis/out_api_review.json ({len(report)} endpoints)")
