"""從 Yahoo Finance 抓 SPX(^GSPC)、ES 期貨(ES=F)、VIX、VIX1D 日 K，存到 cache/。"""
import pandas as pd
import yfinance as yf

from common import CACHE

TICKERS = {"spx": "^GSPC", "es": "ES=F", "vix": "^VIX", "vix1d": "^VIX1D"}


def fetch(start="2015-06-01", save=True):
    out = {}
    for k, t in TICKERS.items():
        df = yf.download(t, start=start, auto_adjust=False, progress=False, multi_level_index=False)
        df = df[["Open", "High", "Low", "Close"]].dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        out[k] = df
        if save:
            df.to_parquet(CACHE / f"yf_{k}.parquet")
    return out


def load():
    return {k: pd.read_parquet(CACHE / f"yf_{k}.parquet") for k in TICKERS}


if __name__ == "__main__":
    d = fetch()
    for k, v in d.items():
        print(k, len(v), v.index.min().date(), v.index.max().date(), v.iloc[-1].round(2).to_dict())
