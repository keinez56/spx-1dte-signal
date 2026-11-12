"""測試在現行賣 put 規則上加賣 call（隔日到期，持有到結算，賣在 bid，扣手續費）。"""
import numpy as np
import pandas as pd

from backtest import stats
from common import CACHE
from market_data import load
from strategy import PARAMS, decide, indicators

START = pd.Timestamp("2016-01-01")
FEE = PARAMS["fee_pts"]

yf = load()
spot = pd.read_parquet(CACHE / "spx_spot.parquet").set_index("date")["spot"].sort_index()
ch = pd.read_parquet(CACHE / "spx_near_chain.parquet")
ch = ch.sort_values("pValue").drop_duplicates(["date", "expir", "strike"], keep="last")
close = yf["spx"]["Close"]
b = yf["spx"]
clpos = (b.Close - b.Low) / (b.High - b.Low)
ind = indicators(spot)
days = list(ind.index)
by = {d: g for d, g in ch.groupby("date")}


def pick_put(g, t):
    g = g[(g.pdelta < 0) & (g.pdelta > -1) & (g.pBidPx > 0)]
    if g.empty:
        return None
    r = g.loc[(g.pdelta + t).abs().idxmin()]
    return r if abs(r.pdelta + t) <= max(0.02, t * 0.35) else None


def pick_call(g, t):
    g = g[(g.delta > 0) & (g.delta < 1) & (g.cBidPx > 0)]
    if g.empty:
        return None
    r = g.loc[(g.delta - t).abs().idxmin()]
    return r if abs(r.delta - t) <= max(0.02, t * 0.35) else None


# 每天的基本資料
base = []
for i, d in enumerate(days[:-1]):
    if d < START or d not in by:
        continue
    e = days[i + 1]
    g = by[d]
    g = g[g.expir == e]
    if g.empty:
        continue
    st = close.get(e, spot.get(e))
    row = ind.loc[d]
    pdl, code, _ = decide(row, e, clpos.get(d, np.nan))
    base.append(dict(d=d, e=e, g=g, st=st, pdl=pdl, code=code, bias=row["bias"], rv=row["rv10"],
                     px=row["px"], ret=np.expm1(row["ret"])))


def run(call_rule, put_on=True):
    out = []
    for x in base:
        pnl, legs = 0.0, 0
        if put_on and x["pdl"] is not None:
            r = pick_put(x["g"], x["pdl"])
            if r is not None:
                pnl += r.pBidPx - max(r.strike - x["st"], 0) - FEE
                legs += 1
        cdl = call_rule(x)
        cprem = closs = 0.0
        if cdl:
            c = pick_call(x["g"], cdl)
            if c is not None:
                cprem = c.cBidPx
                closs = max(x["st"] - c.strike, 0)
                pnl += cprem - closs - FEE
                legs += 1
        if legs:
            out.append(dict(date=x["d"], pnl=pnl, cprem=cprem, closs=closs))
    return pd.DataFrame(out)


def show(name, t):
    s = stats(t, name)
    h1 = stats(t[t.date < "2021-07-01"], "")["sharpe"]
    h2 = stats(t[t.date >= "2021-07-01"], "")["sharpe"]
    return dict(方案=name, 筆數=s["n"], 總點=round(s["total"]), 平均=round(s["avg"], 2), 回撤=round(s["maxdd"]),
                最差=round(s["worst"]), Sharpe=round(s["sharpe"], 2), 前段=round(h1, 2), 後段=round(h2, 2))


rows = [show("現行：只賣 put", run(lambda x: None))]
traded = lambda x: x["pdl"] is not None
for cd in (0.03, 0.05, 0.10, 0.15, 0.20, 0.30):
    rows.append(show(f"put + call {cd*100:.0f}Δ（同一天）", run(lambda x, cd=cd: cd if traded(x) else None)))
# 趨勢對稱：均線之上 call 賣小、之下賣大
for lo, hi in ((0.05, 0.10), (0.05, 0.20), (0.03, 0.15), (0.10, 0.20)):
    rows.append(show(f"call 乖離≥1% 賣 {lo*100:.0f}Δ／<1% 賣 {hi*100:.0f}Δ",
                     run(lambda x, lo=lo, hi=hi: (lo if x["bias"] >= 0.01 else hi) if traded(x) else None)))
# put 被 R2/R3 擋掉的日子也賣 call
for cd in (0.05, 0.10):
    rows.append(show(f"call {cd*100:.0f}Δ，含 R2/R3 不做 put 的日子",
                     run(lambda x, cd=cd: cd if (traded(x) or x["code"] in ("R2", "R3")) else None)))
# 只賣 call（參考）
rows.append(show("只賣 call 10Δ（put 有做的日子）", run(lambda x: 0.10 if traded(x) else None, put_on=False)))
res = pd.DataFrame(rows)
print(res.to_string(index=False))

# call 單腳在不同乖離區間的表現
cl = run(lambda x: 0.10 if traded(x) else None, put_on=False).merge(
    pd.DataFrame([dict(date=x["d"], bias=x["bias"]) for x in base]), on="date")
cl["bucket"] = pd.cut(cl.bias, [-1, -0.02, 0, 0.01, 0.02, 1])
print(cl.groupby("bucket", observed=True).pnl.agg(["count", "mean", "sum", "min"]).round(2))
print(cl.nsmallest(5, "pnl")[["date", "pnl"]])
