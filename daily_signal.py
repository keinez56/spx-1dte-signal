"""每日訊號：收盤前（約 15:45 ET）用 Yahoo 即時價算 R1–R5，輸出 output/signal.json。

今天的價格用 ^GSPC 最新一分鐘價；過去日子用官方收盤。
R5 收盤位置用 SPX 現貨當日高低（使用者經驗：看現貨比 ES 準，回測兩者幾乎相同）。
建議履約價是估算（依回測：價外% ≈ 係數 × VIX），下單請以券商顯示的 delta 為準。
"""
import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from common import OUT
from events import EVENTS
from strategy import CONFIG, PARAMS, decide, indicators, preset_key, preset_name, skipped_event

ET = ZoneInfo("America/New_York")
HOLIDAYS = {pd.Timestamp(x) for x in """
2025-01-01 2025-01-09 2025-01-20 2025-02-17 2025-04-18 2025-05-26 2025-06-19 2025-07-04 2025-09-01 2025-11-27 2025-12-25
2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19 2026-07-03 2026-09-07 2026-11-26 2026-12-25
2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31 2027-06-18 2027-07-05 2027-09-06 2027-11-25 2027-12-24
""".split()}
EARLY_CLOSE = {pd.Timestamp(x) for x in "2026-11-27 2026-12-24 2027-11-26".split()}
# 2023 年起（每天都有到期）回測中位數：價外% / VIX、權利金(bid)佔現價% / VIX
OTM_PER_VIX = {0.04: 0.130, 0.30: 0.0454, 0.20: 0.065}
PREM_PER_VIX = {0.04: 0.000874, 0.30: 0.00864, 0.20: 0.0048}


def estimate(px, vix, delta):
    """估計履約價（往下取 5 的倍數）與權利金。只是估算，實際以券商報價為準。"""
    strike = float(np.floor(px * (1 - OTM_PER_VIX[delta] * vix / 100) / 5) * 5)
    prem = round(px * PREM_PER_VIX[delta] * vix / 100, 2)
    return strike, prem


def next_trading_day(d):
    d = pd.Timestamp(d) + pd.Timedelta(days=1)
    while d.weekday() >= 5 or d in HOLIDAYS:
        d += pd.Timedelta(days=1)
    return d


def is_trading_day(d):
    d = pd.Timestamp(d).normalize()
    return d.weekday() < 5 and d not in HOLIDAYS


def in_session(now_et):
    """美股現貨盤中（09:30 到收盤後 5 分鐘）。"""
    d = pd.Timestamp(now_et.date())
    if not is_trading_day(d):
        return False
    end = time(13, 5) if d in EARLY_CLOSE else time(16, 5)
    return time(9, 30) <= now_et.time() < end


def last_closed_day(now_et):
    """最近一個已經收盤的交易日。"""
    d = pd.Timestamp(now_et.date())
    end = time(13, 5) if d in EARLY_CLOSE else time(16, 5)
    if is_trading_day(d) and now_et.time() >= end:
        return d
    d -= pd.Timedelta(days=1)
    while not is_trading_day(d):
        d -= pd.Timedelta(days=1)
    return d


def es_session_pos(now_et):
    """ES 當日盤（前一天 18:00 ET 起）收盤位置。"""
    m = yf.download("ES=F", period="5d", interval="5m", auto_adjust=False, progress=False, multi_level_index=False)
    if m.empty:
        return np.nan, None
    m.index = m.index.tz_convert(ET)
    # 判斷日 D 的 ES 日 K = D-1 18:00 ET 到 D 17:00 ET（週一自然從週日 18:00 起）
    start = datetime.combine((now_et - pd.Timedelta(days=1)).date(), time(18, 0), ET)
    end = datetime.combine(now_et.date(), time(17, 0), ET)
    s = m[(m.index >= start) & (m.index < end)]
    if s.empty:
        return np.nan, None
    hi, lo, last = s["High"].max(), s["Low"].min(), s["Close"].iloc[-1]
    pos = (last - lo) / (hi - lo) if hi > lo else np.nan
    return float(pos), dict(high=float(hi), low=float(lo), last=float(last))


def history_signals(daily, es_daily, vix, n=60):
    """用日 K 收盤重算近 n 天每天的訊號（含隔天結果），給網頁的歷史表。"""
    ind = indicators(daily["Close"])
    es_pos = (es_daily["Close"] - es_daily["Low"]) / (es_daily["High"] - es_daily["Low"])
    rows = []
    idx = list(ind.index)
    for i, d in enumerate(idx[-n - 1:]):
        k = idx.index(d)
        e = idx[k + 1] if k + 1 < len(idx) else next_trading_day(d)
        delta, code, why = decide(ind.loc[d], e, es_pos.get(d, np.nan))
        nxt = float(daily["Close"].iloc[k + 1] / daily["Close"].iloc[k] - 1) if k + 1 < len(idx) else None
        px = float(ind.loc[d, "px"])
        strike = prem = pnl = None
        v = vix.get(d, np.nan)
        if delta is not None and np.isfinite(v):
            strike, prem = estimate(px, float(v), delta)
            if k + 1 < len(idx):
                settle = float(daily["Close"].iloc[k + 1])
                pnl = round(prem - max(strike - settle, 0) - PARAMS["fee_pts"], 2)
        rows.append(dict(date=f"{d:%Y-%m-%d}", expiry=f"{e:%Y-%m-%d}", delta=delta, code=code, why=why,
                         close=round(px, 2), next_ret=nxt, strike=strike, prem=prem, pnl=pnl))
    return rows[::-1]


def main():
    now_et = datetime.now(ET)
    today = pd.Timestamp(now_et.date())
    daily = yf.download("^GSPC", period="9mo", auto_adjust=False, progress=False, multi_level_index=False)
    daily.index = pd.to_datetime(daily.index).tz_localize(None).normalize()
    es_daily = yf.download("ES=F", period="9mo", auto_adjust=False, progress=False, multi_level_index=False)
    es_daily.index = pd.to_datetime(es_daily.index).tz_localize(None).normalize()
    vix = yf.download("^VIX", period="9mo", auto_adjust=False, progress=False, multi_level_index=False)["Close"]
    vix.index = pd.to_datetime(vix.index).tz_localize(None).normalize()
    vix_now = float(vix.iloc[-1])

    is_trading = today.weekday() < 5 and today not in HOLIDAYS
    intraday = yf.download("^GSPC", period="1d", interval="1m", auto_adjust=False, progress=False, multi_level_index=False)
    closes = daily["Close"].copy()
    if in_session(now_et):
        # 盤中（手動強制更新時）：今天用當下價格
        px_now = float(intraday["Close"].iloc[-1]) if not intraday.empty else float(closes.iloc[-1])
        px_time = intraday.index[-1].tz_convert(ET).strftime("%Y-%m-%d %H:%M ET") if not intraday.empty else None
        closes.loc[today] = px_now
        sig_day = today
    else:
        # 收盤後到隔天開盤前：判斷「最近一個已收盤交易日」，用官方收盤價
        sig_day = last_closed_day(now_et)
        if sig_day in closes.index:
            px_now = float(closes.loc[sig_day])
            px_time = f"{sig_day:%Y-%m-%d} 官方收盤"
        else:  # Yahoo 日 K 還沒出來：先用最後一分鐘價，之後的排程會再用官方收盤價重算
            px_now = float(intraday["Close"].iloc[-1]) if not intraday.empty else float(closes.iloc[-1])
            px_time = f"{sig_day:%Y-%m-%d} 收盤前最後價（官方收盤價尚未發布）"
            closes.loc[sig_day] = px_now
        closes = closes[closes.index <= sig_day]
    closes = closes.sort_index()
    ind = indicators(closes)
    row = ind.loc[sig_day]
    expiry = next_trading_day(sig_day)
    # R5：SPX 現貨當日 K 棒（09:30–16:00）收盤位置
    if sig_day in daily.index:
        b = daily.loc[sig_day]
        hi, lo = float(b["High"]), float(b["Low"])
    elif not intraday.empty:
        hi, lo = float(intraday["High"].max()), float(intraday["Low"].min())
    else:
        hi = lo = np.nan
    hi, lo = max(hi, px_now), min(lo, px_now)
    es_pos = float((px_now - lo) / (hi - lo)) if hi > lo else np.nan
    es_info = dict(high=hi, low=lo, last=px_now)
    delta, code, why = decide(row, expiry, es_pos)

    est = None
    if delta is not None:
        v_sig = float(vix.get(sig_day, vix_now))
        k, prem = estimate(px_now, v_sig, delta)
        est = dict(otm=1 - k / px_now, strike=k, prem=prem)

    HI = round(PARAMS["hi_delta"] * 100)
    checks = [
        dict(rule="R1 事件日", value=EVENTS.get(expiry, "無"), hit=bool(skipped_event(expiry)),
             note=f"到期日 {expiry:%Y-%m-%d} 有沒有 " + ("NFP/FOMC（CPI 日依設定照做）" if PARAMS["trade_cpi"] else "CPI/NFP/FOMC")),
        dict(rule="R2 太平靜", value=f"{row['rv10']:.1%}", hit=bool(row["rv10"] < PARAMS["rv_min"]),
             note="10 日已實現波動 < 9% 不做"),
        dict(rule="R3 反彈陷阱", value=f"今日 {np.expm1(row['ret']):+.2%}／乖離 {row['bias']:+.2%}",
             hit=bool(np.expm1(row["ret"]) > PARAMS["rebound_ret"] and row["bias"] < 0),
             note="漲 > 1.5% 且在 20 日均線下不做"),
        dict(rule="R4 分層", value=f"乖離 {row['bias']:+.2%}（20MA {row['ma20']:.0f}）",
             hit=bool(row["bias"] >= PARAMS["bias_hi"]), note=f"乖離 ≥ 1% 賣 {HI}Δ，否則 4Δ"),
        dict(rule="R5 收低檔", value=(f"{es_pos:.0%}" if np.isfinite(es_pos) else "無資料"),
             hit=bool(np.isfinite(es_pos) and es_pos < PARAMS["es_low_pos"]),
             note=f"SPX 現貨收在當日振幅最低 30% → {HI}Δ 降為 4Δ"),
    ]
    out = dict(
        generated=now_et.strftime("%Y-%m-%d %H:%M ET"), signal_day=f"{sig_day:%Y-%m-%d}",
        expiry=f"{expiry:%Y-%m-%d}", is_trading_day=is_trading, early_close=bool(sig_day in EARLY_CLOSE),
        action="SELL" if delta else "SKIP", delta=delta, code=code, why=why,
        spx=px_now, spx_time=px_time, vix=vix_now, est=est, es=es_info, checks=checks,
        history=history_signals(daily[daily.index <= sig_day], daily, vix),
        config=dict(CONFIG, key=preset_key(**CONFIG), name=preset_name(**CONFIG)),
        upcoming_events=[dict(date=f"{d:%Y-%m-%d}", event=v, skip=bool(skipped_event(d))) for d, v in sorted(EVENTS.items())
                         if today <= d <= today + pd.Timedelta(days=45)],
    )
    (OUT / "signal.json").write_text(json.dumps(out, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
    print(out["signal_day"], "→", out["expiry"], out["action"], delta, why)


if __name__ == "__main__":
    main()
