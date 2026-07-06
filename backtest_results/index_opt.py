import pickle, warnings, json, math; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
raw=pickle.load(open('prices.pkl','rb'))
nif=raw['^NSEI'].dropna(subset=['Close']); vix=raw['^INDIAVIX']['Close'].dropna()
nif=nif[nif.index>=pd.Timestamp('2019-01-01')]
c,o,h,l=nif.Close,nif.Open,nif.High,nif.Low

# why MR never fires
print("=== MR sleeve diagnosis (55 stocks, 2019-2026) ===")
import sys; sys.path.insert(0,'/sessions/loving-tender-noether/mnt/trading_bot')
panels=pickle.load(open('panels.pkl','rb')) if False else None

# 1) Dual Donchian on Nifty (20-high entry / 10-low exit), next-open fills
hi20=h.rolling(20).max().shift(1); lo10=l.rolling(10).min().shift(1)
pos=0; entry=0; trades=[]
dates=c.index
for i in range(21,len(dates)-1):
    d=dates[i]; nxt=dates[i+1]
    if pos==0 and c.iloc[i]>hi20.iloc[i]:
        pos=1; entry=o.iloc[i+1]*1.0005; edate=nxt
    elif pos==1 and c.iloc[i]<lo10.iloc[i]:
        ex=o.iloc[i+1]*0.9995
        trades.append({'entry_date':edate.date(),'exit_date':nxt.date(),'ret':(ex-entry)/entry}); pos=0
if pos==1: trades.append({'entry_date':edate.date(),'exit_date':dates[-1].date(),'ret':(c.iloc[-1]-entry)/entry})
T=pd.DataFrame(trades)
eq=(1+T.ret).cumprod()
years=(dates[-1]-dates[21]).days/365.25
bh=(c.iloc[-1]/c.iloc[21])**(1/years)-1
strat_cagr=eq.iloc[-1]**(1/years)-1
exp_curve=(1+T.set_index('exit_date').ret).cumprod(); peak=exp_curve.cummax()
print("=== NIFTY dual Donchian 20/10 (long-only, 5bp slip/side) ===")
print(f"trades {len(T)}, win% {(T.ret>0).mean()*100:.0f}, avg {T.ret.mean()*100:.2f}%, CAGR {strat_cagr*100:.1f}% vs buy-hold {bh*100:.1f}%, maxDD {((exp_curve-peak)/peak).min()*100:.1f}%, time-in-mkt {T.shape[0]*T.ret.notna().mean():.0f} trades")

# 2) Golden-cross regime switch (50/200)
ma50=c.rolling(50).mean(); ma200=c.rolling(200).mean()
sig=(ma50>ma200).shift(1).fillna(False)
ret=c.pct_change().fillna(0)
strat=(1+ret[sig]).prod(); n_days=sig.sum()
sr=(ret[sig]).mean()/ (ret[sig]).std()*math.sqrt(252)
gc_cagr=strat**(252/max(n_days,1))-1
eqgc=(1+ret.where(sig,0)).cumprod(); pk=eqgc.cummax()
print(f"=== Golden cross 50/200 switch === CAGR-while-invested {gc_cagr*100:.1f}%, overall {((eqgc.iloc[-1])**(1/years)-1)*100:.1f}%/yr, maxDD {((eqgc-pk)/pk).min()*100:.1f}%, invested {sig.mean()*100:.0f}% of days, Sharpe(inv) {sr:.2f}")

# 3) Weekly pin study (book claim: expiry Thu close within ±200 pts of prior Fri open ~80%)
wk=pd.DataFrame({'o':o,'c':c}); wk['dow']=wk.index.weekday
fri_open=wk[wk.dow==4].o; thu_close=wk[wk.dow==3].c
rows=[]
for fd,fo in fri_open.items():
    nxt_thu=thu_close[(thu_close.index>fd)&(thu_close.index<=fd+pd.Timedelta(days=7))]
    if len(nxt_thu):
        tc=nxt_thu.iloc[0]; rows.append({'fri':fd.date(),'move':tc-fo,'pct':abs(tc-fo)/fo})
P=pd.DataFrame(rows)
print(f"=== Weekly pin (n={len(P)} weeks 2019-2026) ===")
print(f"|move|<=200pts: {(P.move.abs()<=200).mean()*100:.0f}%  |move|<=1.5%: {(P.pct<=0.015).mean()*100:.0f}%  |move|<=1.0%: {(P.pct<=0.010).mean()*100:.0f}%  median|move| {P.move.abs().median():.0f} pts ({P.pct.median()*100:.2f}%)")
for y,g in P.groupby(pd.to_datetime(P.fri).dt.year): print(f"  {y}: <=200pts {(g.move.abs()<=200).mean()*100:.0f}%  <=1.5% {(g.pct<=0.015).mean()*100:.0f}%")

# 4) Synthetic monthly ATM straddle (BS, IV=India VIX at entry) — LOW/MED confidence
from math import log,sqrt,exp,erf
N=lambda x:0.5*(1+erf(x/sqrt(2)))
def bs(S,K,T,r,iv,cp):
    if T<=0: return max(0.0,(S-K) if cp=='c' else (K-S))
    d1=(log(S/K)+(r+iv*iv/2)*T)/(iv*sqrt(T)); d2=d1-iv*sqrt(T)
    return S*N(d1)-K*exp(-r*T)*N(d2) if cp=='c' else K*exp(-r*T)*N(-d2)-S*N(-d1)
months=pd.period_range('2019-01','2026-05',freq='M')
res=[]
for m in months:
    days=c[(c.index.to_period('M')==m)]
    if len(days)<15: continue
    d0=days.index[0]
    thus=[d for d in days.index if d.weekday()==3]
    if not thus: continue
    dT=thus[-1]
    S0=c[d0]; K=round(S0/50)*50; T=(dT-d0).days/365
    iv=vix.asof(d0)/100
    if pd.isna(iv): continue
    prem=bs(S0,K,T,0.065,iv,'c')+bs(S0,K,T,0.065,iv,'p')
    payoff=abs(c[dT]-K)
    res.append({'m':str(m),'prem':prem,'payoff':payoff,'long_pnl':payoff-prem,'iv':iv*100})
ST=pd.DataFrame(res)
costs=0.002  # ~premium txn costs both legs round trip (approx)
print(f"=== Synthetic monthly ATM straddle, n={len(ST)} months (SYNTHETIC BS pricing, IV=VIX — MEDIUM/LOW confidence) ===")
print(f"LONG straddle: mean P&L {ST.long_pnl.mean():.0f} pts/mo, win% {(ST.long_pnl>0).mean()*100:.0f}, total {ST.long_pnl.sum():.0f} pts")
print(f"SHORT straddle: mean {(-ST.long_pnl).mean():.0f} pts/mo, win% {(ST.long_pnl<0).mean()*100:.0f}, total {-ST.long_pnl.sum():.0f} pts, worst month {-ST.long_pnl.max():.0f}")
by_y=ST.groupby(ST.m.str[:4]).long_pnl.sum().round(0)
print("LONG by year:",dict(by_y))
print(f"avg premium charged: {ST.prem.mean():.0f} pts = {(ST.prem/ (ST.payoff+ST.prem)).mean()*100:.0f}% rich vs payoff | avg VIX {ST.iv.mean():.1f}")
