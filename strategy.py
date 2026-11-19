"""最終規則 R1–R5（回測與每日訊號共用）。

每個交易日收盤後判斷（回測用 ORATS 15:45 快照），賣「下一個交易日到期」的 SPX put 1 口，持有到結算。
R1 事件日不做：到期日（隔天）有 NFP / FOMC；CPI 依設定決定做不做
R2 太平靜不做：10 日已實現波動 < 9%
R3 反彈陷阱不做：今天漲 > 1.5% 且價格在 20 日均線之下
R4 分層：價格比 20 日均線高 >= 1% → 賣高檔 delta，否則低檔（4Δ）
R5 收低檔降級：SPX 現貨當日 K 棒收在振幅最低 30% → 高檔降為低檔

可切換的設定放在 strategy_config.json（改完 push，網頁訊號就會跟著換；回測四種組合都已預先算好）：
  hi_delta   高檔賣的 delta：0.30（現行版）或 0.20（保守版）
  trade_cpi  false = CPI 日不做（現行版）；true = 只有 CPI 的日子照常做（NFP、FOMC 仍不做）
也可用環境變數 STRAT_HI_DELTA、STRAT_TRADE_CPI（1/0）臨時覆蓋。
"""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from events import EVENTS

HI_CHOICES = (0.20, 0.30)
DEFAULT_CONFIG = dict(hi_delta=0.30, trade_cpi=False)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    f = Path(__file__).resolve().parent / "strategy_config.json"
    if f.exists():
        cfg.update({k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items() if k in cfg})
    if os.environ.get("STRAT_HI_DELTA"):
        cfg["hi_delta"] = float(os.environ["STRAT_HI_DELTA"])
    if os.environ.get("STRAT_TRADE_CPI"):
        cfg["trade_cpi"] = os.environ["STRAT_TRADE_CPI"].strip().lower() in ("1", "true", "yes")
    cfg["hi_delta"] = float(cfg["hi_delta"])
    cfg["trade_cpi"] = bool(cfg["trade_cpi"])
    if cfg["hi_delta"] not in HI_CHOICES:
        raise SystemExit(f"strategy_config.json 的 hi_delta 只能是 {HI_CHOICES}，現在是 {cfg['hi_delta']}")
    return cfg


def preset_key(hi_delta, trade_cpi):
    """回測結果的索引鍵，例如 h30、h30c、h20、h20c。"""
    return f"h{round(hi_delta * 100)}{'c' if trade_cpi else ''}"


def preset_name(hi_delta, trade_cpi):
    return f"4Δ/{round(hi_delta * 100)}Δ，CPI 日{'照做' if trade_cpi else '不做'}"


CONFIG = load_config()
BASE = dict(rv_min=0.09, rebound_ret=0.015, ma_len=20, bias_hi=0.01, lo_delta=0.04, es_low_pos=0.30, fee_pts=0.0282)
PARAMS = dict(BASE, hi_delta=CONFIG["hi_delta"], trade_cpi=CONFIG["trade_cpi"])


def make_params(hi_delta, trade_cpi, **over):
    return dict(BASE, hi_delta=hi_delta, trade_cpi=trade_cpi, **over)


def skipped_event(expiry, p=PARAMS):
    """到期日有需要避開的事件就回傳事件名稱，否則回傳 None。只有 CPI 且 trade_cpi 時不避開。"""
    ev = EVENTS.get(pd.Timestamp(expiry).normalize())
    if not ev:
        return None
    if p.get("trade_cpi") and set(ev.split("/")) == {"CPI"}:
        return None
    return ev


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
    ev = skipped_event(expiry, p)
    if ev:
        return None, "R1", f"到期日 {pd.Timestamp(expiry):%m/%d} 有 {ev}，事件日不做"
    if not np.isfinite(row["rv10"]) or not np.isfinite(row["bias"]):
        return None, "NA", "歷史資料不足"
    if row["rv10"] < p["rv_min"]:
        return None, "R2", f"10 日已實現波動 {row['rv10']:.1%} < {p['rv_min']:.0%}，太平靜不做"
    simple_ret = np.expm1(row["ret"])
    if simple_ret > p["rebound_ret"] and row["bias"] < 0:
        return None, "R3", f"今天漲 {simple_ret:+.2%} 但仍在 20 日均線下（乖離 {row['bias']:+.2%}），反彈陷阱不做"
    cpi = "（到期日有 CPI，依設定照做）" if EVENTS.get(pd.Timestamp(expiry).normalize()) else ""
    if row["bias"] >= p["bias_hi"]:
        if es_clpos is not None and np.isfinite(es_clpos) and es_clpos < p["es_low_pos"]:
            return p["lo_delta"], "R5", (f"乖離 {row['bias']:+.2%} ≥ 1% 本應賣 {p['hi_delta']*100:.0f}Δ，"
                                         f"但 SPX 收在當日振幅 {es_clpos:.0%} 處（低檔），降為 {p['lo_delta']*100:.0f}Δ{cpi}")
        return p["hi_delta"], "R4H", f"乖離 {row['bias']:+.2%} ≥ 1%，賣 {p['hi_delta']*100:.0f}Δ{cpi}"
    return p["lo_delta"], "R4L", f"乖離 {row['bias']:+.2%} < 1%，只賣 {p['lo_delta']*100:.0f}Δ{cpi}"
