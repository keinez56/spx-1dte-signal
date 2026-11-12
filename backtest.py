"""2016 起回測最終方案與對照組，輸出 output/backtest.json 給儀表板。

結算價：SPX 官方收盤（Yahoo ^GSPC），找不到才用 ORATS 隔天快照。
同一履約價有 AM/PM 兩條鏈（月選到期日）時取價值較高的那條（PM 結算的 SPXW）。
"""
import json

import numpy as np
import pandas as pd

from common import CACHE, OUT
from market_data import load
from strategy import PARAMS, decide, indicators

START = pd.Timestamp("2016-01-01")


def load_chain():
    c = pd.read_parquet(CACHE / "spx_near_puts.parquet")
    c = c[(c["pdelta"] < 0) & (c["pdelta"] > -1) & (c["pBidPx"] > 0) & (c["pAskPx"] >= c["pBidPx"])]
    c = c.sort_values("pValue").drop_duplicates(["date", "expir", "strike"], keep="last")
    return c


def pick(chain_day, target):
    """選 put delta 最接近 -target 的履約價。"""
    i = (chain_day["pdelta"] + target).abs().idxmin()
    r = chain_day.loc[i]
    if abs(r["pdelta"] + target) > max(0.02, target * 0.35):
        return None
    return r


def stats(tr, name):
    pnl = tr["pnl"]
    eq = pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    daily = pnl  # 每筆即一天
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
    yrs = (tr["date"].max() - tr["date"].min()).days / 365.25
    return dict(name=name, n=int(len(tr)), win=float((pnl > 0).mean()), avg=float(pnl.mean()),
                total=float(pnl.sum()), per_year=float(pnl.sum() / yrs), maxdd=float(dd),
                worst=float(pnl.min()), sharpe=float(sharpe))


def account_sim(tr, spx_close, capital=100_000, margin_mult=20):
    """起始資金、口數 = floor(資金 / (SPX×20))，獲利滾入。"""
    cap = capital
    rows = []
    for r in tr.itertuples():
        m = spx_close.asof(r.date) * margin_mult
        n = max(int(cap // m), 0)
        cap += n * r.pnl * 100
        rows.append((r.date, cap, n))
    df = pd.DataFrame(rows, columns=["date", "cap", "n"])
    yrs = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    cagr = (df["cap"].iloc[-1] / capital) ** (1 / yrs) - 1
    dd = (df["cap"] / df["cap"].cummax() - 1).min()
    return df, float(cagr), float(dd)


def main():
    yf = load()
    spot = pd.read_parquet(CACHE / "spx_spot.parquet").set_index("date")["spot"].sort_index()
    chain = load_chain()
    close = yf["spx"]["Close"]
    import os
    es = yf[os.environ.get("R5_SRC", "spx")]  # R5 用哪個日 K：es（ES 期貨）或 spx（現貨）
    es_pos = ((es["Close"] - es["Low"]) / (es["High"] - es["Low"])).replace([np.inf, -np.inf], np.nan)

    ind = indicators(spot)
    days = list(ind.index)
    by_day = {d: g for d, g in chain.groupby("date")}
    variants = {
        "final": dict(PARAMS),
        "cons20": dict(PARAMS, hi_delta=0.20),
    }
    trades = {k: [] for k in variants}
    trades["fixed5"] = []
    trades["fixed30"] = []
    log = []
    for i, d in enumerate(days[:-1]):
        if d < START:
            continue
        e = days[i + 1]
        g = by_day.get(d)
        if g is None:
            continue
        g = g[g["expir"] == e]
        if g.empty:
            continue
        settle = close.get(e, np.nan)
        if not np.isfinite(settle):
            settle = spot.get(e)
        row = ind.loc[d]
        clp = es_pos.get(d, np.nan)
        for k, p in variants.items():
            delta, code, why = decide(row, e, clp, p)
            if k == "final":
                log.append(dict(date=d, expir=e, code=code, delta=delta, why=why))
            if delta is None:
                continue
            r = pick(g, delta)
            if r is None:
                continue
            pnl = r["pBidPx"] - max(r["strike"] - settle, 0) - p["fee_pts"]
            trades[k].append(dict(date=d, expir=e, delta=delta, code=code, strike=r["strike"], spot=row["px"],
                                  bid=r["pBidPx"], settle=settle, otm=1 - r["strike"] / row["px"], pnl=pnl))
        # 對照：固定 delta、只跳事件日
        from events import EVENTS
        if pd.Timestamp(e) not in EVENTS:
            for k, dl in (("fixed5", 0.05), ("fixed30", 0.30)):
                r = pick(g, dl)
                if r is not None:
                    pnl = r["pBidPx"] - max(r["strike"] - settle, 0) - PARAMS["fee_pts"]
                    trades[k].append(dict(date=d, expir=e, delta=dl, code="F", strike=r["strike"], spot=row["px"],
                                          bid=r["pBidPx"], settle=settle, otm=1 - r["strike"] / row["px"], pnl=pnl))

    names = {"final": "最終方案 30Δ/4Δ（R1–R5）", "cons20": "保守版 20Δ/4Δ（R1–R5）",
             "fixed5": "固定 5Δ（只跳事件日）", "fixed30": "固定 30Δ（只跳事件日）"}
    out = {"generated": pd.Timestamp.now().isoformat(timespec="minutes"), "params": PARAMS,
           "data_end": str(spot.index.max().date()), "summary": [], "curves": {}, "yearly": {}}
    tdf = {k: pd.DataFrame(v) for k, v in trades.items()}
    for k, t in tdf.items():
        s = stats(t, names[k])
        h1, h2 = t[t["date"] < "2021-07-01"], t[t["date"] >= "2021-07-01"]
        s["sharpe_h1"] = stats(h1, "")["sharpe"]
        s["sharpe_h2"] = stats(h2, "")["sharpe"]
        sim, cagr, add = account_sim(t, close)
        s["cagr_100k"], s["acct_dd"], s["final_cap"] = cagr, add, float(sim["cap"].iloc[-1])
        out["summary"].append(s)
        out["curves"][k] = {"d": t["date"].dt.strftime("%Y-%m-%d").tolist(),
                            "eq": t["pnl"].cumsum().round(2).tolist()}
        y = t.groupby(t["date"].dt.year)["pnl"].agg(["count", "sum"]).round(1)
        out["yearly"][k] = {int(i): [int(a), float(b)] for i, (a, b) in y.iterrows()}
        if k == "final":
            out["acct_curve"] = {"d": sim["date"].dt.strftime("%Y-%m-%d").tolist(),
                                 "cap": sim["cap"].round(0).tolist(), "n": sim["n"].tolist()}
    f = tdf["final"]
    f["dd"] = f["pnl"].cumsum() - f["pnl"].cumsum().cummax()
    out["final_dd"] = f["dd"].round(2).tolist()
    out["final_delta"] = f["delta"].tolist()
    out["worst"] = f.nsmallest(10, "pnl").assign(date=lambda x: x["date"].dt.strftime("%Y-%m-%d"),
                                                 expir=lambda x: x["expir"].dt.strftime("%Y-%m-%d")).round(3).to_dict("records")
    lg = pd.DataFrame(log)
    out["rule_counts"] = lg["code"].value_counts().to_dict()
    out["by_code"] = f.groupby("code")["pnl"].agg(["count", "mean", "sum"]).round(2).reset_index().to_dict("records")
    out["otm_median"] = {str(k): float(v) for k, v in f.groupby("delta")["otm"].median().items()}

    # 規則消融：一次拿掉一條
    abl = []
    for nm, mod in (("全部規則", {}), ("拿掉 R2（太平靜）", dict(rv_min=-1)),
                    ("拿掉 R3（反彈陷阱）", dict(rebound_ret=9)), ("拿掉 R5（收低檔）", dict(es_low_pos=-1))):
        p = dict(PARAMS, **mod)
        rs = []
        for i, d in enumerate(days[:-1]):
            if d < START or d not in by_day:
                continue
            e = days[i + 1]
            g = by_day[d]
            g = g[g["expir"] == e]
            if g.empty:
                continue
            delta, _, _ = decide(ind.loc[d], e, es_pos.get(d, np.nan), p)
            if delta is None:
                continue
            r = pick(g, delta)
            if r is None:
                continue
            settle = close.get(e, spot.get(e))
            rs.append(dict(date=d, pnl=r["pBidPx"] - max(r["strike"] - settle, 0) - p["fee_pts"]))
        abl.append(stats(pd.DataFrame(rs), nm))
    out["ablation"] = abl
    if os.environ.get("NO_WRITE"):
        for s_ in out["summary"] + abl:
            print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s_.items() if k in ("name","n","total","maxdd","worst","sharpe","sharpe_h1","sharpe_h2","cagr_100k")})
        return
    (OUT / "backtest.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    f.to_csv(OUT / "trades_final.csv", index=False, encoding="utf-8-sig")
    for s in out["summary"] + abl:
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()})
    print(out["rule_counts"])


if __name__ == "__main__":
    main()
