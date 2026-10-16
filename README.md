# BTC Futures Signal Research — analysis only

No orders are placed. Uses public Bybit USDT-M OHLCV.

- `run_analysis.py`: closed-candle signal scan.
- `backtest.py`: preliminary OHLCV backtest.
- `.github/workflows/run-analysis.yml`: manual/scheduled workflow.

This is research software, not a profitable strategy guarantee. Funding, liquidation, exchange lot-size filters, market impact and latency are not fully modeled. Backtest history is limited by the exchange OHLCV endpoint and requires independent out-of-sample validation. GitHub Actions is not real-time and scheduled runs can be delayed.

Upload these files to the repository, preserving folder paths. Then choose Actions → BTC Signal and Backtest → Run workflow. Inspect `latest_signal.md` and `backtest_results.json`.


## 데이터 제공처 변경

기본 데이터 제공처를 Bybit USDT 선형 무기한 계약 공개 API로 설정했습니다. GitHub Actions 실행 환경에서 거래소별 지역 제한이 발생할 수 있으므로, 첫 실행에서 `restricted location`, HTTP 403/451 등이 발생하면 로그를 확인하고 다른 공개 데이터 제공처로 전환해야 합니다. 이 코드는 공개 시세 데이터만 조회하며 주문을 제출하지 않습니다.
