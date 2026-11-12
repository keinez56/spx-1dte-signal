"""從 ORATS 每日 zip/csv 抽出 SPX：每日現價 + 7 天內到期的 put 鏈。

按欄名讀（2024-11-27 起多 cOpra/pOpra 欄）。put delta = delta - 1。
用法: python extract_spx.py [--data-dir PATH] [--start 2015-10-01] [--workers 12]
"""
import argparse
import io
import re
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from common import CACHE, data_dir

COLS = ["stkPx", "expirDate", "strike", "pBidPx", "pAskPx", "pValue", "pMidIv",
        "delta", "trade_date", "pOi", "pVolu"]


def read_spx_block(path: Path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            raw = z.read(z.namelist()[0])
    else:
        raw = path.read_bytes()
    nl = raw.find(b"\n")
    header = raw[:nl].decode().strip()
    i = raw.find(b"\nSPX,")
    if i < 0:
        return None
    j = raw.rfind(b"\nSPX,")
    k = raw.find(b"\n", j + 1)
    block = raw[i + 1: (k if k > 0 else len(raw))]
    df = pd.read_csv(io.BytesIO(header.encode() + b"\n" + block), usecols=lambda c: c in COLS)
    return df


def process(path_str):
    path = Path(path_str)
    m = re.search(r"(\d{8})", path.name)
    d = pd.Timestamp(m.group(1))
    try:
        df = read_spx_block(path)
    except Exception as e:  # 壞檔
        return d, None, None, f"{path.name}: {e}"
    if df is None or df.empty:
        return d, None, None, f"{path.name}: no SPX"
    spot = float(df["stkPx"].median())
    df["expir"] = pd.to_datetime(df["expirDate"], format="%m/%d/%Y")
    df["dte"] = (df["expir"] - d).dt.days
    near = df[(df["dte"] >= 0) & (df["dte"] <= 7)].copy()
    near["pdelta"] = near["delta"] - 1.0
    near["date"] = d
    near = near[["date", "expir", "dte", "strike", "stkPx", "pBidPx", "pAskPx", "pValue",
                 "pMidIv", "pdelta", "pOi", "pVolu"]]
    return d, spot, near, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir")
    ap.add_argument("--start", default="2015-10-01")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    root = data_dir(a.data_dir)
    start = pd.Timestamp(a.start)
    files = {}
    for p in sorted(root.glob("*/ORATS_SMV_Strikes_*")):
        m = re.search(r"(\d{8})", p.name)
        if not m:
            continue
        d = pd.Timestamp(m.group(1))
        if d < start:
            continue
        # 同一天 zip 與 csv 都有時取 zip
        if d not in files or p.suffix == ".zip":
            files[d] = p
    print(f"{len(files)} files from {root}")
    spots, chains, errs = [], [], []
    with ProcessPoolExecutor(a.workers) as ex:
        for n, (d, spot, near, err) in enumerate(ex.map(process, [str(files[k]) for k in sorted(files)], chunksize=4)):
            if err:
                errs.append(err)
                continue
            spots.append((d, spot))
            chains.append(near)
            if n % 200 == 0:
                print(n, d.date(), spot, flush=True)
    pd.DataFrame(spots, columns=["date", "spot"]).to_parquet(CACHE / "spx_spot.parquet")
    pd.concat(chains).to_parquet(CACHE / "spx_near_puts.parquet")
    print("errors:", len(errs), errs[:10])


if __name__ == "__main__":
    main()
