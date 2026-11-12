"""共用設定：資料路徑可用 --data-dir、環境變數 ORATS_DATA_DIR 或 config.json 指定。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
OUT = ROOT / "output"
CACHE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

DEFAULT_DIRS = [
    r"D:\Claude\orats_option_historical_data",            # 公司電腦
    r"C:\Users\User\Desktop\orats-option history data",   # 家裡電腦
]


def data_dir(cli_value=None):
    if cli_value:
        return Path(cli_value)
    if os.environ.get("ORATS_DATA_DIR"):
        return Path(os.environ["ORATS_DATA_DIR"])
    cfg = ROOT / "config.json"
    if cfg.exists():
        v = json.loads(cfg.read_text(encoding="utf-8")).get("orats_data_dir")
        if v:
            return Path(v)
    for d in DEFAULT_DIRS:
        if Path(d).exists():
            return Path(d)
    raise SystemExit("找不到 ORATS 資料夾，請用 --data-dir、ORATS_DATA_DIR 或 config.json 指定")
