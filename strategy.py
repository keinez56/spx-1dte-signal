"""最終規則 R1–R5（回測與每日訊號共用）。

每個交易日收盤後判斷（回測用 ORATS 15:45 快照），賣「下一個交易日到期」的 SPX put 1 口，持有到結算。
R1 事件日不做：到期日（隔天）有 CPI / NFP / FOMC
R2 太平靜不做：10 日已實現波動 < 9%
R3 反彈陷阱不做：今天漲 > 1.5% 且價格在 20 日均線之下
R4 分層：價格比 20 日均線高 >= 1% → 賣高檔 delta（30Δ），否則低檔（4Δ）
R5 收低檔降級：SPX 現貨當日 K 棒收在振幅最低 30% → 高檔降為低檔
"""
import numpy as np
import pandas as pd

from events import EVENTS

PARAMS = dict(rv_min=0.09, rebound_ret=0.015, ma_len=20, bias_hi=0.01,
              hi_delta=0.30, lo_delta=0.04, es_low_pos=0.30, fee_pts=0.0282)


def indicators(px: pd.Series, p=PARAMS):
    """px: 每日判斷時點價格（回測用 ORATS 15:45 快照；即時用當下價格）。"""
    df = pd.DataFrame({"px": px})
    df["ret"] = np.log(df["px"]).diff()
    df["rv10"] = df["ret"].rolling(10).std(ddof=1) * np.sqrt(252)
    df["ma20"] = df["px"].rolling(p["ma_len"]).mean()
    df["bias"] = df["px"] / df["ma20"] - 1
    return df


def decide(row, expiry, es_clpos, p=PARAMS):
    """回傳 (delta 或 None, 規則代碼, 中文原因)。"""
    ev = EVENTS.get(pd.Timestamp(expiry).normalize())
    if ev:
        return None, "R1", f"到期日 {pd.Timestamp(expiry):%m/%d} 有 {ev}，事件日不做"
    if not np.isfinite(row["rv10"]) or not np.isfinite(row["bias"]):
        return None, "NA", "歷史資料不足"
    if row["rv10"] < p["rv_min"]:
        return None, "R2", f"10 日已實現波動 {row['rv10']:.1%} < {p['rv_min']:.0%}，太平靜不做"
    simple_ret = np.expm1(row["ret"])
    if simple_ret > p["rebound_ret"] and row["bias"] < 0:
        return None, "R3", f"今天漲 {simple_ret:+.2%} 但仍在 20 日均線下（乖離 {row['bias']:+.2%}），反彈陷阱不做"
    if row["bias"] >= p["bias_hi"]:
        if es_clpos is not None and np.isfinite(es_clpos) and es_clpos < p["es_low_pos"]:
            return p["lo_delta"], "R5", (f"乖離 {row['bias']:+.2%} ≥ 1% 本應賣 {p['hi_delta']*100:.0f}Δ，"
                                         f"但 SPX 收在當日振幅 {es_clpos:.0%} 處（低檔），降為 {p['lo_delta']*100:.0f}Δ")
        return p["hi_delta"], "R4H", f"乖離 {row['bias']:+.2%} ≥ 1%，賣 {p['hi_delta']*100:.0f}Δ"
    return p["lo_delta"], "R4L", f"乖離 {row['bias']:+.2%} < 1%，只賣 {p['lo_delta']*100:.0f}Δ"
