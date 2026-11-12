"""Run the analyst across segments and themes; write a compact findings summary."""
import json, subprocess, sys, time, re
from pathlib import Path
OUT = Path(sys.argv[1])
RUNS = [
 ("chips_world", ["screen", "--industry", "Semiconductors", "--score", "8"]),
 ("banks_india", ["screen", "--industry", "bank", "--market", "in", "--score", "8"]),
 ("software_us", ["screen", "--industry", "software", "--market", "us", "--score", "8"]),
 ("pharma_world_value", ["screen", "--industry", "drug manufacturers", "--score", "8", "--rank", "valuation"]),
 ("industrials_japan", ["screen", "--sector", "industrials", "--market", "jp", "--score", "8"]),
 ("energy_world", ["screen", "--sector", "energy", "--score", "8"]),
 ("insurance_europe", ["screen", "--industry", "insurance", "--market", "gb", "de", "fr", "--score", "8"]),
 ("reits_us", ["screen", "--sector", "real estate", "--market", "us", "--score", "6"]),
 ("gold_world", ["screen", "--industry", "gold", "--score", "6"]),
 ("utilities_hk_cn", ["screen", "--sector", "utilities", "--market", "hk", "cn", "--score", "6"]),
 ("smallcaps_tech_under_500m", ["screen", "--sector", "tech", "--max-mcap", "500M", "--score", "6"]),
 ("beaten_down_consumer", ["screen", "--sector", "consumer", "--min-drawdown", "30", "--score", "6"]),
 ("theme_ai_infra", ["compare", "NVDA", "AVGO", "ANET", "VRT", "SMCI", "CBRS", "2330.TW", "000660.KS", "6857.T"]),
 ("theme_ev", ["compare", "TSLA", "1211.HK", "RIVN", "LCID", "300750.SZ", "ALB", "TATAMOTORS.NS", "NIO"]),
 ("theme_defence", ["compare", "LMT", "RHM.DE", "BA.L", "HAL.NS", "012450.KS", "KTOS", "SAAB-B.ST"]),
 ("theme_recent_ipos", ["compare", "SPCX", "CBRS", "ARM", "RDDT", "KVYO", "BIRK"]),
]
PLAIN = [("pick", ["pick", "--amount", "10L"]), ("brief", ["brief"]),
         ("ipo_oura", ["ipo", "oura", "--json"]), ("ipo_nonsense", ["ipo", "zzqx nonexistent co", "--json"]),
         ("analyse_bank_us", ["analyse", "JPM"]), ("analyse_adr", ["analyse", "BABA"]),
         ("analyse_london_pence", ["analyse", "SHEL.L"]), ("analyse_tiny", ["analyse", "3640.T"])]

def run(args, timeout=420):
    t = time.time()
    try:
        p = subprocess.run([sys.executable, "-m", "fundamental_engine", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, round(time.time() - t, 1)
    except subprocess.TimeoutExpired:
        return -9, "", "TIMEOUT", timeout

def card_flags(c):
    f = []
    r = c.get("risk", {}); ev = " | ".join(c["why"]["fundamentals"] + c["why"]["valuation"])
    sig = " | ".join(s["message"] for s in c["early_signals"])
    if re.search(r"\bnan\b|\binf\b", (ev + sig).lower()): f.append("nan/inf in text")
    if r.get("volatility_1y_pct") is None: f.append("volatility unknown")
    if r.get("traded_per_day_base") is None: f.append("traded/day unknown")
    if c.get("market_cap_base") is None: f.append("market cap unknown")
    if c.get("valuation") is None: f.append("valuation unscored")
    fy, ly = r.get("fiscal_years") or 0, r.get("loss_years") or 0
    if c["fundamentals"] >= 85 and fy and ly / fy >= 0.5: f.append(f"fund {c['fundamentals']} with {ly}/{fy} loss years")
    if c["fundamentals"] >= 85 and any(s["severity"] == "urgent" and s["tone"] == "concern" for s in c["early_signals"]): f.append("fund>=85 with URGENT concern")
    if (c.get("valuation") or 0) >= 85 and any(s["tone"] == "concern" and s["severity"] in ("urgent", "alert") for s in c["early_signals"]): f.append(f"valuation {c['valuation']} with material concern")
    m = re.search(r"Not scored for lack of data \((\d+)%", ev)
    if m and int(m.group(1)) >= 35: f.append(f"{m.group(1)}% of rubric unscored")
    if "short record" in ev: f.append("2y growth fallback")
    if c["risk_adjusted"] < 0: f.append("risk-adjusted negative")
    pe = re.search(r"P/E ([\d.]+)", ev)
    if pe and float(pe.group(1)) < 4: f.append(f"P/E {pe.group(1)} suspiciously low")
    if r.get("volatility_1y_pct") and r["volatility_1y_pct"] > 150: f.append(f"volatility {r['volatility_1y_pct']}%")
    return f

summary = []
for name, args in RUNS:
    code, out, err, secs = run([*args, "--json"])
    row = {"run": name, "cmd": " ".join(args), "exit": code, "seconds": secs}
    errs = [l for l in err.splitlines() if re.search(r"Traceback|Error|WARNING|TIMEOUT", l)]
    row["stderr"] = errs[-4:]; row["stderr_lines"] = len(errs)
    try:
        d = json.loads(out[out.index("{"):])
        for k in ("companies_matched", "read_whole", "ordered_by", "unread_markets", "size_mix", "scored_size_mix"):
            if k in d: row[k] = d[k]
        row["not_scored"] = [f"{x.get('ticker')}:{x.get('status')}" for x in d.get("not_scored", [])]
        row["cards"] = [{"t": c["ticker"], "name": c["company"][:28], "F": c["fundamentals"], "V": c["valuation"], "adj": c["risk_adjusted"],
                         "disc": c["risk_discount"], "verdict": c["verdict"], "band": c.get("size_band"), "flags": card_flags(c)} for c in d.get("scored", [])]
    except Exception as e:
        row["parse_error"] = f"{type(e).__name__}: {e}"; row["stdout_tail"] = out[-300:]
    summary.append(row); OUT.write_text(json.dumps(summary, indent=1))
for name, args in PLAIN:
    code, out, err, secs = run(args)
    errs = [l for l in (err + out).splitlines() if re.search(r"Traceback|Error\b|TIMEOUT|\bnan\b|\binf\b", l)]
    summary.append({"run": name, "cmd": " ".join(args), "exit": code, "seconds": secs, "problems": errs[-5:], "out_chars": len(out),
                    "tail": [l for l in out.splitlines() if l.strip()][-3:]})
    OUT.write_text(json.dumps(summary, indent=1))
print("done", len(summary))
