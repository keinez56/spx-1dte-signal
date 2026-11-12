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
from strategy import PARAMS, decide, indicators

ET = ZoneInfo("America/New_York")
HOLIDAYS = {pd.Timestamp(x) for x in """
2025-01-01 2025-01-09 2025-01-20 2025-02-17 2025-04-18 2025-05-26 2025-06-19 2025-07-04 2025-09-01 2025-11-27 2025-12-25
2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19 2026-07-03 2026-09-07 2026-11-26 2026-12-25
2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31 2027-06-18 2027-07-05 2027-09-06 2027-11-25 2027-12-24
""".split()}
EARLY_CLOSE = {pd.Timestamp(x) for x in "2026-11-27 2026-12-24 2027-11-26".split()}
OTM_PER_VIX = {0.04: 0.1193, 0.30: 0.0190, 0.20: 0.040}  # 回測中位數：價外% / VIX


def next_trading_day(d):
    d = pd.Timestamp(d) + pd.Timedelta(days=1)
    while d.weekday() >= 5 or d in HOLIDAYS:
        d += pd.Timedelta(days=1)
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


def history_signals(daily, es_daily, n=60):
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
        rows.append(dict(date=f"{d:%Y-%m-%d}", expiry=f"{e:%Y-%m-%d}", delta=delta, code=code, why=why,
                         close=round(float(ind.loc[d, 'px']), 2), next_ret=nxt))
    return rows[::-1]


def main():
    now_et = datetime.now(ET)
    today = pd.Timestamp(now_et.date())
    daily = yf.download("^GSPC", period="9mo", auto_adjust=False, progress=False, multi_level_index=False)
    daily.index = pd.to_datetime(daily.index).tz_localize(None).normalize()
    es_daily = yf.download("ES=F", period="9mo", auto_adjust=False, progress=False, multi_level_index=False)
    es_daily.index = pd.to_datetime(es_daily.index).tz_localize(None).normalize()
    vix = yf.download("^VIX", period="5d", auto_adjust=False, progress=False, multi_level_index=False)["Close"]
    vix_now = float(vix.iloc[-1])

    is_trading = today.weekday() < 5 and today not in HOLIDAYS
    intraday = yf.download("^GSPC", period="1d", interval="1m", auto_adjust=False, progress=False, multi_level_index=False)
    px_now = float(intraday["Close"].iloc[-1]) if not intraday.empty else float(daily["Close"].iloc[-1])
    px_time = intraday.index[-1].tz_convert(ET).strftime("%Y-%m-%d %H:%M ET") if not intraday.empty else None
    # 收盤後（16:05 ET 以後）或休市日：改用官方收盤價（含收盤競價），不用最後一分鐘的成交價
    last_day = daily.index[-1]
    if (now_et.time() >= time(16, 5) and last_day == today) or not is_trading:
        px_now = float(daily["Close"].iloc[-1])
        px_time = f"{last_day:%Y-%m-%d} 官方收盤"

    closes = daily["Close"].copy()
    if is_trading:
        closes.loc[today] = px_now  # 今天用當下價格
        sig_day = today
    else:
        sig_day = closes.index[-1]
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
        otm = OTM_PER_VIX[delta] * vix_now / 100
        k = px_now * (1 - otm)
        est = dict(otm=otm, strike=float(np.floor(k / 5) * 5))

    checks = [
        dict(rule="R1 事件日", value=EVENTS.get(expiry, "無"), hit=bool(EVENTS.get(expiry)),
             note=f"到期日 {expiry:%Y-%m-%d} 有沒有 CPI/NFP/FOMC"),
        dict(rule="R2 太平靜", value=f"{row['rv10']:.1%}", hit=bool(row["rv10"] < PARAMS["rv_min"]),
             note="10 日已實現波動 < 9% 不做"),
        dict(rule="R3 反彈陷阱", value=f"今日 {np.expm1(row['ret']):+.2%}／乖離 {row['bias']:+.2%}",
             hit=bool(np.expm1(row["ret"]) > PARAMS["rebound_ret"] and row["bias"] < 0),
             note="漲 > 1.5% 且在 20 日均線下不做"),
        dict(rule="R4 分層", value=f"乖離 {row['bias']:+.2%}（20MA {row['ma20']:.0f}）",
             hit=bool(row["bias"] >= PARAMS["bias_hi"]), note="乖離 ≥ 1% 賣 30Δ，否則 4Δ"),
        dict(rule="R5 收低檔", value=(f"{es_pos:.0%}" if np.isfinite(es_pos) else "無資料"),
             hit=bool(np.isfinite(es_pos) and es_pos < PARAMS["es_low_pos"]),
             note="SPX 現貨收在當日振幅最低 30% → 30Δ 降為 4Δ"),
    ]
    out = dict(
        generated=now_et.strftime("%Y-%m-%d %H:%M ET"), signal_day=f"{sig_day:%Y-%m-%d}",
        expiry=f"{expiry:%Y-%m-%d}", is_trading_day=is_trading, early_close=bool(sig_day in EARLY_CLOSE),
        action="SELL" if delta else "SKIP", delta=delta, code=code, why=why,
        spx=px_now, spx_time=px_time, vix=vix_now, est=est, es=es_info, checks=checks,
        history=history_signals(daily, daily),
        upcoming_events=[dict(date=f"{d:%Y-%m-%d}", event=v) for d, v in sorted(EVENTS.items())
                         if today <= d <= today + pd.Timedelta(days=45)],
    )
    (OUT / "signal.json").write_text(json.dumps(out, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
    print(out["signal_day"], "→", out["expiry"], out["action"], delta, why)


if __name__ == "__main__":
    main()
