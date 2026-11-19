"""2016 起回測，輸出 output/backtest.json 給儀表板。

四種設定（高檔 30Δ/20Δ × CPI 日做/不做）都會算，網頁依 strategy_config.json 的設定顯示對應那一組，
所以切換設定後不必重跑回測。另有固定 5Δ、固定 30Δ 兩個對照組。

結算價：SPX 官方收盤（Yahoo ^GSPC），找不到才用 ORATS 隔天快照。
同一履約價有 AM/PM 兩條鏈（月選到期日）時取價值較高的那條（PM 結算的 SPXW）。
"""
import json

import numpy as np
import pandas as pd

from common import CACHE, OUT
from market_data import load
from strategy import CONFIG, HI_CHOICES, PARAMS, decide, indicators, make_params, preset_key, preset_name

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


def run(days, by_day, ind, es_pos, close, spot, p, log=None):
    """跑一組參數，回傳逐筆交易 DataFrame。"""
    rs = []
    for i, d in enumerate(days[:-1]):
        if d < START or d not in by_day:
            continue
        e = days[i + 1]
        g = by_day[d]
        g = g[g["expir"] == e]
        if g.empty:
            continue
        settle = close.get(e, np.nan)
        if not np.isfinite(settle):
            settle = spot.get(e)
        row = ind.loc[d]
        delta, code, why = decide(row, e, es_pos.get(d, np.nan), p)
        if log is not None:
            log.append(dict(date=d, expir=e, code=code, delta=delta, why=why))
        if delta is None:
            continue
        r = pick(g, delta)
        if r is None:
            continue
        pnl = r["pBidPx"] - max(r["strike"] - settle, 0) - p["fee_pts"]
        rs.append(dict(date=d, expir=e, delta=delta, code=code, strike=r["strike"], spot=row["px"],
                       bid=r["pBidPx"], settle=settle, otm=1 - r["strike"] / row["px"], pnl=pnl))
    return pd.DataFrame(rs)


def run_fixed(days, by_day, ind, close, spot, dl):
    """對照組：固定 delta、只跳全部事件日。"""
    from events import EVENTS
    rs = []
    for i, d in enumerate(days[:-1]):
        if d < START or d not in by_day:
            continue
        e = days[i + 1]
        g = by_day[d]
        g = g[g["expir"] == e]
        if g.empty or pd.Timestamp(e) in EVENTS:
            continue
        settle = close.get(e, np.nan)
        if not np.isfinite(settle):
            settle = spot.get(e)
        r = pick(g, dl)
        if r is None:
            continue
        rs.append(dict(date=d, expir=e, delta=dl, code="F", strike=r["strike"], spot=ind.loc[d, "px"], bid=r["pBidPx"],
                       settle=settle, otm=1 - r["strike"] / ind.loc[d, "px"], pnl=r["pBidPx"] - max(r["strike"] - settle, 0) - PARAMS["fee_pts"]))
    return pd.DataFrame(rs)


def main():
    import os
    yf = load()
    spot = pd.read_parquet(CACHE / "spx_spot.parquet").set_index("date")["spot"].sort_index()
    chain = load_chain()
    close = yf["spx"]["Close"]
    es = yf[os.environ.get("R5_SRC", "spx")]  # R5 用哪個日 K：es（ES 期貨）或 spx（現貨）
    es_pos = ((es["Close"] - es["Low"]) / (es["High"] - es["Low"])).replace([np.inf, -np.inf], np.nan)
    ind = indicators(spot)
    days = list(ind.index)
    by_day = {d: g for d, g in chain.groupby("date")}
    args = (days, by_day, ind, es_pos, close, spot)

    active = preset_key(CONFIG["hi_delta"], CONFIG["trade_cpi"])
    presets = [(hi, cpi) for hi in sorted(HI_CHOICES, reverse=True) for cpi in (False, True)]
    out = {"generated": pd.Timestamp.now().isoformat(timespec="minutes"), "params": PARAMS, "config": CONFIG, "active": active,
           "data_end": str(spot.index.max().date()), "order": [], "summary": [], "curves": {}, "yearly": {}, "detail": {}}

    def add(key, name, t, sim=True):
        s = stats(t, name)
        s["key"] = key
        h1, h2 = t[t["date"] < "2021-07-01"], t[t["date"] >= "2021-07-01"]
        s["sharpe_h1"], s["sharpe_h2"] = stats(h1, "")["sharpe"], stats(h2, "")["sharpe"]
        acct, cagr, add_ = account_sim(t, close)
        s["cagr_100k"], s["acct_dd"], s["final_cap"] = cagr, add_, float(acct["cap"].iloc[-1])
        out["order"].append(key)
        out["summary"].append(s)
        out["curves"][key] = {"d": t["date"].dt.strftime("%Y-%m-%d").tolist(), "eq": t["pnl"].cumsum().round(2).tolist()}
        y = t.groupby(t["date"].dt.year)["pnl"].agg(["count", "sum"]).round(1)
        out["yearly"][key] = {int(i): [int(a), float(b)] for i, (a, b) in y.iterrows()}
        return acct

    tdf_active = None
    for hi, cpi in presets:
        key, p, log = preset_key(hi, cpi), make_params(hi, cpi), []
        t = run(*args, p, log)
        acct = add(key, preset_name(hi, cpi), t)
        t["dd"] = t["pnl"].cumsum() - t["pnl"].cumsum().cummax()
        abl = [stats(t, "全部規則")]
        for nm, pp in (("拿掉 R1（事件日全部照做）", None), ("拿掉 R2（太平靜）", dict(rv_min=-1)),
                       ("拿掉 R3（反彈陷阱）", dict(rebound_ret=9)), ("拿掉 R5（收低檔）", dict(es_low_pos=-1))):
            if pp is None:
                import strategy
                saved = strategy.EVENTS
                strategy.EVENTS = {}
                try:
                    abl.append(stats(run(*args, p), nm))
                finally:
                    strategy.EVENTS = saved
            else:
                abl.append(stats(run(*args, dict(p, **pp)), nm))
        out["detail"][key] = dict(
            acct_curve={"d": acct["date"].dt.strftime("%Y-%m-%d").tolist(), "cap": acct["cap"].round(0).tolist(), "n": acct["n"].tolist()},
            dd=t["dd"].round(2).tolist(), delta=t["delta"].tolist(),
            worst=t.nsmallest(10, "pnl").assign(date=lambda x: x["date"].dt.strftime("%Y-%m-%d"),
                                                expir=lambda x: x["expir"].dt.strftime("%Y-%m-%d")).round(3).to_dict("records"),
            rule_counts=pd.DataFrame(log)["code"].value_counts().to_dict(),
            by_code=t.groupby("code")["pnl"].agg(["count", "mean", "sum"]).round(2).reset_index().to_dict("records"),
            otm_median={str(k): float(v) for k, v in t.groupby("delta")["otm"].median().items()}, ablation=abl)
        if key == active:
            tdf_active = t
    add("fixed5", "固定 5Δ（只跳事件日）", run_fixed(days, by_day, ind, close, spot, 0.05))
    add("fixed30", "固定 30Δ（只跳事件日）", run_fixed(days, by_day, ind, close, spot, 0.30))

    show = ("key", "name", "n", "total", "maxdd", "worst", "sharpe", "sharpe_h1", "sharpe_h2", "cagr_100k")
    for s_ in out["summary"]:
        print(("* " if s_["key"] == active else "  ") + str({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s_.items() if k in show}))
    for s_ in out["detail"][active]["ablation"]:
        print("   ", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s_.items() if k in show})
    print(out["detail"][active]["rule_counts"])
    if os.environ.get("NO_WRITE"):
        return
    (OUT / "backtest.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    tdf_active.to_csv(OUT / "trades_final.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
