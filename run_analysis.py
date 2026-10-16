import json, os, traceback
from datetime import datetime, timezone
import ccxt
import pandas as pd

SYMBOL = os.getenv("SYMBOL", "BTC/USDT:USDT")
EQUITY = float(os.getenv("ACCOUNT_EQUITY_USDT", "1000"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.5"))  # percent, not fraction
MAX_NOTIONAL_MULTIPLE = float(os.getenv("MAX_NOTIONAL_MULTIPLE", "1.0"))
FEE = float(os.getenv("FEE_RATE", "0.0005"))
SLIPPAGE = float(os.getenv("SLIPPAGE_RATE", "0.0002"))

def fetch(ex, tf, limit=300):
    rows = ex.fetch_ohlcv(SYMBOL, tf, limit=limit)
    d = pd.DataFrame(rows, columns=["timestamp","open","high","low","close","volume"])
    d["timestamp"] = pd.to_datetime(d["timestamp"], unit="ms", utc=True)
    d = d.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")
    if len(d) and int(d.index[-1].timestamp()*1000) + ex.parse_timeframe(tf)*1000 > ex.milliseconds():
        d = d.iloc[:-1]  # exclude currently forming candle
    if len(d) < 80: raise RuntimeError(f"Not enough closed candles for {tf}: {len(d)}")
    return d

def indicators(d):
    d=d.copy()
    d["ema20"]=d.close.ewm(span=20,adjust=False).mean()
    d["ema50"]=d.close.ewm(span=50,adjust=False).mean()
    return d

def main():
    if not (0 < RISK_PCT <= 0.5): raise ValueError("RISK_PCT must be >0 and <=0.5 percent")
    if EQUITY <= 0 or MAX_NOTIONAL_MULTIPLE <= 0: raise ValueError("Equity and notional cap must be positive")
    if not (0 <= FEE < .02 and 0 <= SLIPPAGE < .02): raise ValueError("Invalid fee/slippage")
    ex=ccxt.bybit({"enableRateLimit":True,"timeout":20000,"options":{"defaultType":"swap"}})
    try:
        d4=indicators(fetch(ex,"4h")); d1=indicators(fetch(ex,"1h")); d15=indicators(fetch(ex,"15m"))
        now=datetime.now(timezone.utc).isoformat()
        last4=d4.iloc[-1]
        if last4.close > last4.ema20 > last4.ema50 and last4.ema20 > d4.ema20.iloc[-4]:
            side="LONG"; trend=True
        elif last4.close < last4.ema20 < last4.ema50 and last4.ema20 < d4.ema20.iloc[-4]:
            side="SHORT"; trend=True
        else: side="NEUTRAL"; trend=False
        out={"generated_at_utc":now,"symbol":SYMBOL,"status":"NO_SIGNAL","direction":side,
             "reason":"4h trend not aligned" if not trend else "Waiting for 1h pullback and 15m breakout-retest",
             "entry":None,"stop":None,"target_2r":None,"risk_pct":RISK_PCT,"account_equity_usdt":EQUITY,
             "analysis_only":True}
        if trend:
            h1,h1p=d1.iloc[-1],d1.iloc[-2]
            pull=(h1p.low <= h1p.ema20*1.003 and h1.close > h1.ema20) if side=="LONG" else (h1p.high >= h1p.ema20*.997 and h1.close < h1.ema20)
            if pull:
                level_window=d15.iloc[-10:-2]; breakout=d15.iloc[-2]; trigger=d15.iloc[-1]
                if side=="LONG":
                    level=float(level_window.high.max())
                    valid=breakout.close>level and trigger.low<=level*1.0015 and trigger.close>level
                    entry=float(trigger.close)*(1+SLIPPAGE)
                    stop=min(float(trigger.low),float(h1p.low))*(1-SLIPPAGE)
                    valid=valid and stop<entry
                else:
                    level=float(level_window.low.min())
                    valid=breakout.close<level and trigger.high>=level*.9985 and trigger.close<level
                    entry=float(trigger.close)*(1-SLIPPAGE)
                    stop=max(float(trigger.high),float(h1p.high))*(1+SLIPPAGE)
                    valid=valid and stop>entry
                if valid:
                    risk_dist=abs(entry-stop)
                    risk_budget=EQUITY*RISK_PCT/100
                    risk_per_btc=risk_dist+entry*(2*FEE+2*SLIPPAGE)
                    qty=min(risk_budget/risk_per_btc, EQUITY*MAX_NOTIONAL_MULTIPLE/entry)
                    target=entry+2*(entry-stop) if side=="LONG" else entry-2*(stop-entry)
                    out.update(status="SIGNAL",reason="4h trend + 1h pullback + 15m breakout-retest confirmed",
                        breakout_level=round(level,2),entry=round(entry,2),stop=round(stop,2),target_2r=round(target,2),
                        quantity_btc=round(qty,6),estimated_notional_usdt=round(qty*entry,2),
                        estimated_risk_including_costs_usdt=round(qty*risk_per_btc,4),
                        signal_candle_15m_utc=trigger.name.isoformat())
                else: out["reason"]="1h pullback present; 15m breakout-retest absent"
        with open("latest_signal.json","w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
        lines=["# BTC/USDT 무기한 선물 분석 결과","",f"- 생성 시각(UTC): {out['generated_at_utc']}",f"- 상태: **{out['status']}**",
               f"- 방향: **{out['direction']}**",f"- 사유: {out['reason']}",""]
        if out["status"]=="SIGNAL":
            lines += [f"- 진입 참고가: `{out['entry']}`",f"- 손절 참고가: `{out['stop']}`",
                      f"- 2R 목표가: `{out['target_2r']}`",f"- 수량 추정: `{out['quantity_btc']} BTC`",
                      f"- 비용 포함 위험 추정: `${out['estimated_risk_including_costs_usdt']}`",""]
        lines += ["> 분석 전용. 주문 없음. 수익 보장 아님. 펀딩비·청산·거래소 주문 필터·시장충격은 완전히 모델링하지 않음."]
        open("latest_signal.md","w",encoding="utf-8").write("\n".join(lines)+"\n")
        print(json.dumps(out,ensure_ascii=False,indent=2))
    except Exception as e:
        out={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"symbol":SYMBOL,
             "status":"ERROR","reason":f"{type(e).__name__}: {e}","analysis_only":True}
        with open("latest_signal.json","w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
        open("latest_signal.md","w",encoding="utf-8").write("# BTC/USDT 분석 오류\n\n"+out["reason"]+"\n")
        traceback.print_exc()
        raise

if __name__=="__main__": main()
