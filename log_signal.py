"""把 output/signal.json 的當日結論附加到 history/signals.csv（同一判斷日只留最新一筆）。"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
s = json.loads((ROOT / "output" / "signal.json").read_text(encoding="utf-8"))
f = ROOT / "history" / "signals.csv"
f.parent.mkdir(exist_ok=True)
cols = ["signal_day", "expiry", "action", "delta", "code", "spx", "vix", "est_strike", "why", "generated"]
rows = []
if f.exists():
    rows = [r for r in csv.DictReader(f.open(encoding="utf-8")) if r["signal_day"] != s["signal_day"]]
rows.append(dict(signal_day=s["signal_day"], expiry=s["expiry"], action=s["action"], delta=s["delta"] or "",
                 code=s["code"], spx=round(s["spx"], 2), vix=round(s["vix"], 2),
                 est_strike=(s["est"] or {}).get("strike", ""), why=s["why"], generated=s["generated"]))
rows.sort(key=lambda r: r["signal_day"])
with f.open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
print("logged", s["signal_day"], s["action"], s["delta"])
