"""排程入口：只在美東現貨收盤後（16:05–20:00 ET）跑訊號並重建網頁。
訊號用 SPX 官方收盤價；ES 日 K 用到當下（16:15 時離 ES 17:00 收盤還有 45 分鐘）。
用法: python run_daily.py [--force]
輸出最後一行：RESULT=UPDATED 或 RESULT=SKIP <原因>
"""
import subprocess
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from daily_signal import EARLY_CLOSE, HOLIDAYS

ET = ZoneInfo("America/New_York")
now = datetime.now(ET)
d = pd.Timestamp(now.date())
force = "--force" in sys.argv
if not force:
    if d.weekday() >= 5 or d in HOLIDAYS:
        print("RESULT=SKIP 今天美股休市"); sys.exit(0)
    lo, hi = time(16, 5), time(20, 0)
    if not (lo <= now.time() <= hi):
        print(f"RESULT=SKIP 現在 {now:%H:%M} ET 不在收盤後時段"); sys.exit(0)
    # 今天已經用收盤價更新過就跳過（排程一天會試很多次）
    try:
        import json, urllib.request
        url = "https://keinez56.github.io/spx-1dte-signal/signal.json"
        live = json.load(urllib.request.urlopen(url, timeout=20))
        if live.get("signal_day") == f"{d:%Y-%m-%d}" and "官方收盤" in str(live.get("spx_time")):
            print("RESULT=SKIP 今天已更新過"); sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:  # 讀不到就照跑
        print("檢查線上版本失敗，照常更新：", e)
py = sys.executable
for s in ("daily_signal.py", "build_page.py"):
    r = subprocess.run([py, s], cwd=str(__import__('pathlib').Path(__file__).parent))
    if r.returncode != 0:
        print(f"RESULT=ERROR {s} 失敗"); sys.exit(1)
print("RESULT=UPDATED")
