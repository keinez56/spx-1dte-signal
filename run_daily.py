"""排程入口：算「最近一個已收盤交易日」的訊號並重建網頁。

GitHub 的排程常延遲好幾個小時或漏跑，所以排程每小時都會試一次；
這裡只負責判斷要不要真的更新：
- 美股盤中不更新（等收盤）
- 線上網頁已經是最近一個收盤日的官方收盤訊號 → 跳過
其他時間（收盤後到隔天開盤前，含週末）都會補上最近一個收盤日的訊號。
用法: python run_daily.py [--force]
輸出最後一行：RESULT=UPDATED 或 RESULT=SKIP <原因>
"""
import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_signal import in_session, last_closed_day

ET = ZoneInfo("America/New_York")
now = datetime.now(ET)
force = "--force" in sys.argv
if not force:
    if in_session(now):
        print(f"RESULT=SKIP 現在 {now:%H:%M} ET 美股盤中，等收盤後再算"); sys.exit(0)
    target = f"{last_closed_day(now):%Y-%m-%d}"
    try:
        url = "https://keinez56.github.io/spx-1dte-signal/signal.json"
        live = json.load(urllib.request.urlopen(url, timeout=20))
        if live.get("signal_day") == target and "官方收盤" in str(live.get("spx_time")):
            print(f"RESULT=SKIP {target} 的訊號已經是最新"); sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:  # 讀不到就照跑
        print("檢查線上版本失敗，照常更新：", e)
py = sys.executable
for s in ("daily_signal.py", "build_page.py"):
    r = subprocess.run([py, s], cwd=str(Path(__file__).parent))
    if r.returncode != 0:
        print(f"RESULT=ERROR {s} 失敗"); sys.exit(1)
print("RESULT=UPDATED")
