# SPX 1DTE 賣 put 每日訊號

網頁：https://claude.ai/artifact/M1D4rRYcPKEpK8gkBSi8bg

ORATS 資料路徑依序找：`--data-dir` 參數 → 環境變數 `ORATS_DATA_DIR` → `config.json` 的 `orats_data_dir` → 公司 `D:\Claude\orats_option_historical_data` / 家裡 `C:\Users\User\Desktop\orats-option history data`。

| 檔案 | 用途 |
|---|---|
| extract_spx.py | 從 ORATS 日檔抽 SPX 現價與 7 天內到期 put 鏈 → cache/（約 3 分鐘） |
| market_data.py | Yahoo 抓 ^GSPC、ES=F、VIX、VIX1D 日 K → cache/ |
| events.py | CPI / NFP / FOMC 日期（2015–2027，來源 BLS、Fed） |
| strategy.py | R1–R5 規則（回測與每日訊號共用） |
| backtest.py | 2016 起回測 → output/backtest.json、trades_final.csv |
| daily_signal.py | 收盤前即時訊號 → output/signal.json |
| build_page.py | 合成 output/dashboard.html |
| run_daily.py | 排程入口，只在 15:25–16:30 ET 執行（--force 強制） |

重建全部：`python extract_spx.py && python market_data.py && python backtest.py && python daily_signal.py && python build_page.py`
