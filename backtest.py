import argparse, json
from datetime import datetime, timezone
import ccxt, numpy as np, pandas as pd

FEE=0.0005
SLIP=0.0002
RISK_PCT=0.5
INITIAL_EQUITY=1000.0

def fetch(ex,tf,limit):
    rows=ex.fetch_ohlcv("BTC/USDT:USDT",tf,limit=limit)
    d=pd.DataFrame(rows,columns=["timestamp","open","high","low","close","volume"])
    d["timestamp"]=pd.to_datetime(d.timestamp,unit="ms",utc=True)
    d=d.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")
    if len(d) and int(d.index[-1].timestamp()*1000)+ex.parse_timeframe(tf)*1000>ex.milliseconds(): d=d.iloc[:-1]
    return d

def ind(d):
    d=d.copy(); d["ema20"]=d.close.ewm(span=20,adjust=False).mean(); d["ema50"]=d.close.ewm(span=50,adjust=False).mean()
    return d

def main():
    p=argparse.ArgumentParser(); p.add_argument("--limit",type=int,default=1000); a=p.parse_args()
    ex=ccxt.bybit({"enableRateLimit":True,"timeout":20000,"options":{"defaultType":"swap"}})
    d15=ind(fetch(ex,"15m",a.limit)); d1=ind(fetch(ex,"1h",min(a.limit,1000))); d4=ind(fetch(ex,"4h",min(a.limit,1000)))
    # Higher-timeframe data is made available only after each candle closes.
    h1=d1.copy(); h1.index=h1.index+pd.Timedelta(hours=1)
    h4=d4.copy(); h4.index=h4.index+pd.Timedelta(hours=4)
    x=d15.reset_index().sort_values("timestamp")
    x=pd.merge_asof(x,h1[["ema20","low","high"]].reset_index().rename(columns={"timestamp":"t1","ema20":"ema20_1h","low":"low_1h","high":"high_1h"}).sort_values("t1"),left_on="timestamp",right_on="t1",direction="backward")
    x=pd.merge_asof(x,h4[["close","ema20","ema50"]].reset_index().rename(columns={"timestamp":"t4","close":"close_4h","ema20":"ema20_4h","ema50":"ema50_4h"}).sort_values("t4"),left_on="timestamp",right_on="t4",direction="backward")
    x=x.set_index("timestamp")
    # Prior closed 4h EMA slope; no future candle data is used.
    slope=h4.ema20.diff(3); slope.index=slope.index+pd.Timedelta(hours=4)
    x=pd.merge_asof(x.reset_index().sort_values("timestamp"),slope.rename("slope4").reset_index().rename(columns={"timestamp":"tslope"}).sort_values("tslope"),left_on="timestamp",right_on="tslope",direction="backward").set_index("timestamp")
    x["long_trend"]=(x.close_4h>x.ema20_4h)&(x.ema20_4h>x.ema50_4h)&(x.slope4>0)
    x["short_trend"]=(x.close_4h<x.ema20_4h)&(x.ema20_4h<x.ema50_4h)&(x.slope4<0)
    # Approximate pullback flag from the most recently closed 1h candle.
    x["pull_long"]=(x.low_1h<=x.ema20_1h*1.003)&(x.close>x.ema20_1h)
    x["pull_short"]=(x.high_1h>=x.ema20_1h*.997)&(x.close<x.ema20_1h)
    x["level_high"]=x.high.shift(2).rolling(8).max()
    x["level_low"]=x.low.shift(2).rolling(8).min()
    x["long_signal"]=x.long_trend & x.pull_long & (x.close.shift(1)>x.level_high) & (x.low<=x.level_high*1.0015)&(x.close>x.level_high)
    x["short_signal"]=x.short_trend & x.pull_short & (x.close.shift(1)<x.level_low) & (x.high>=x.level_low*.9985)&(x.close<x.level_low)

    equity=INITIAL_EQUITY; peak=equity; maxdd=0; trades=[]; i=40
    while i<len(x)-1:
        r=x.iloc[i]
        if not (bool(r.long_signal) or bool(r.short_signal)): i+=1; continue
        side="LONG" if bool(r.long_signal) else "SHORT"; nxt=x.iloc[i+1]
        entry=float(nxt.open)*(1+SLIP if side=="LONG" else 1-SLIP)
        if side=="LONG": stop=min(float(r.low),float(r.low_1h))*(1-SLIP)
        else: stop=max(float(r.high),float(r.high_1h))*(1+SLIP)
        if (side=="LONG" and stop>=entry) or (side=="SHORT" and stop<=entry): i+=1; continue
        target=entry+2*(entry-stop) if side=="LONG" else entry-2*(stop-entry)
        risk_per_btc=abs(entry-stop)+entry*(2*FEE+2*SLIP)
        qty=min(equity*RISK_PCT/100/risk_per_btc,equity/entry)
        exitp=None; reason=None; j=i+1
        for j in range(i+1,len(x)):
            b=x.iloc[j]
            if side=="LONG":
                if b.low<=stop: exitp=stop*(1-SLIP); reason="STOP"
                elif b.high>=target: exitp=target*(1-SLIP); reason="TARGET"
            else:
                if b.high>=stop: exitp=stop*(1+SLIP); reason="STOP"
                elif b.low<=target: exitp=target*(1+SLIP); reason="TARGET"
            if reason: break
        if exitp is None: exitp=float(x.iloc[-1].close)*(1-SLIP if side=="LONG" else 1+SLIP); reason="END_OF_DATA"; j=len(x)-1
        gross=(exitp-entry)*qty*(1 if side=="LONG" else -1); fees=qty*(entry+exitp)*FEE; net=gross-fees
        equity+=net; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak if peak else 0)
        trades.append({"entry_time":x.index[i].isoformat(),"exit_time":x.index[j].isoformat(),"side":side,"entry":round(entry,2),"stop":round(stop,2),"target":round(target,2),"exit":round(exitp,2),"exit_reason":reason,"qty_btc":round(qty,6),"gross_pnl":round(gross,4),"fees":round(fees,4),"net_pnl":round(net,4),"equity_after":round(equity,4)})
        i=max(i+1,j)
    wins=[t for t in trades if t["net_pnl"]>0]; losses=[t for t in trades if t["net_pnl"]<0]
    gp=sum(t["net_pnl"] for t in wins); gl=abs(sum(t["net_pnl"] for t in losses))
    out={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"symbol":"BTC/USDT:USDT","timeframe":"15m","initial_equity_usdt":INITIAL_EQUITY,"final_equity_usdt":round(equity,4),"net_return_pct":round((equity/INITIAL_EQUITY-1)*100,4),"trade_count":len(trades),"win_rate_pct":round(100*len(wins)/len(trades),2) if trades else 0,"profit_factor":round(gp/gl,4) if gl else None,"max_drawdown_pct":round(maxdd*100,4),"fees_per_side":FEE,"slippage_per_side":SLIP,"limitations":["Exchange OHLCV history is limited; this is not a multi-year test.","Funding, liquidation, latency, market impact and exchange filters are omitted.","If stop and target are both touched in one candle, stop is assumed first.","The multi-timeframe pullback mapping is approximate and requires code review and independent testing."],"trades":trades}
    with open("backtest_results.json","w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in out.items() if k!="trades"},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
