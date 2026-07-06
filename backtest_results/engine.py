"""V9 momentum system backtest — faithful to morning_scan.py rules.
No lookahead: signal on bar t close -> fill at bar t+1 open (with gap rejection + slippage).
Costs: trading_bot/cost_model.py (entry+exit) applied to every trade."""
import sys, json, pickle, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '/sessions/loving-tender-noether/mnt/trading_bot')
from cost_model import transaction_cost

CAPITAL=500_000; RISK=20_000; MAX_POS=3; TIME_STOP=12
SLIP={'NIFTY50':0.001,'MIDCAP':0.002,'SMALLCAP':0.004}
GAP_CHASE=0.015; GAP_DOWN=-0.05; ALLOC_CAP=0.8*CAPITAL; MAX_ALLOC_PER=50_000

meta=json.load(open('/tmp/bt/meta.json'))
raw=pickle.load(open('/tmp/bt/prices.pkl','rb'))

def rsi(close,n=14):
    d=close.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean()
    dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+up/dn)
def atr(df,n=14):
    tr=pd.concat([df.High-df.Low,(df.High-df.Close.shift()).abs(),(df.Low-df.Close.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

# ---------- indicators & scores per symbol (fully vectorised) ----------
panels={}; rets12={}
for t in raw.columns.get_level_values(0).unique():
    if not t.endswith('.NS'): continue
    sym=t[:-3]; df=raw[t].dropna(subset=['Close']).copy()
    if len(df)<300: continue
    c,v=df.Close,df.Volume
    df['ma20']=c.rolling(20).mean(); df['ma50']=c.rolling(50).mean()
    df['a1']=((c>df.ma50)&(df.ma20>df.ma50))*2
    df['a2']=((df.ma20>df.ma20.shift(5))&(df.ma50>df.ma50.shift(20)))*1
    df['rsi']=rsi(c); df['c1']=df.rsi.between(40,65)*1
    df['h52']=df.High.rolling(252).max(); df['l52']=df.Low.rolling(252).min()
    df['b2']=(((df.h52-c)/df.h52)<=0.25)*1; df['b3']=(c>=1.3*df.l52)*1
    vavg=v.rolling(20).mean(); df['d1']=(v/vavg>=1.0)*1
    obv=(v*np.sign(c.diff()).fillna(0)).cumsum(); df['d2']=(obv>obv.shift(10))*1
    bo=(df.High>=df.High.rolling(20).max())&(v>=1.5*vavg)
    df['d3']=(bo.rolling(5).max().fillna(0))*1
    pr=((df.High-df.Low)/c).rolling(20).mean(); df['e1']=pr.between(0.015,0.055)*1
    df['atr']=atr(df)
    mid=c.rolling(20).mean(); sd=c.rolling(20).std()
    df['mr']=((c<=(mid-2*sd)*1.005)&(df.rsi<35)&(c>df.ma50))
    panels[sym]=df; rets12[sym]=c/c.shift(252)-1

R=pd.DataFrame(rets12)
rank_pct=R.rank(axis=1,pct=True)*100   # within-universe percentile (survivorship: documented)
b1=pd.DataFrame(np.select([rank_pct>=75,rank_pct>=50],[2,1],0),index=R.index,columns=R.columns)

nif=raw['^NSEI'].dropna(subset=['Close'])
nma50=nif.Close.rolling(50).mean(); nma200=nif.Close.rolling(200).mean()
regime=((nif.Close>nma50)*1+((nif.Close>nma200)&(nma200>nma200.shift(20)))*1)
thr=regime.map({0:11,1:10,2:9})

score={}
for s,df in panels.items():
    sc=(df.a1+df.a2+df.b2+df.b3+b1[s].reindex(df.index).fillna(0)+df.c1+df.d1+df.d2+df.d3+df.e1).astype(float)
    score[s]=sc+regime.reindex(df.index).fillna(0)
S=pd.DataFrame(score)

dates=[d for d in nif.index if d>=pd.Timestamp('2019-06-03')]
def run(costs_on=True,sector_cap=1):
    cash_committed=0; positions=[]; trades=[]
    for i,d in enumerate(dates[:-1]):
        nxt=dates[i+1]
        # ---- manage exits on bar d (positions opened before d) ----
        for p in positions[:]:
            sym=p['sym']; df=panels[sym]
            if d not in df.index: continue
            row=df.loc[d]; p['days']+=1
            exit_px=None; reason=None
            if row.Open<=p['sl']: exit_px,reason=row.Open,'SL_GAP'
            elif row.Low<=p['sl']: exit_px,reason=p['sl'],'SL'
            elif row.Open>=p['tg']: exit_px,reason=row.Open,'TARGET_GAP'
            elif row.High>=p['tg']: exit_px,reason=p['tg'],'TARGET'
            elif p['days']>=TIME_STOP: exit_px,reason=row.Close,'TIME'
            if exit_px:
                slip=SLIP[meta[sym]['tier']]; exit_px*=(1-slip)
                cost=transaction_cost('EQUITY','BUY',p['entry'],p['qty'])+transaction_cost('EQUITY','SELL',exit_px,p['qty']) if costs_on else 0
                pnl=(exit_px-p['entry'])*p['qty']-cost
                trades.append({**p,'exit':round(exit_px,2),'exit_date':d.date(),'reason':reason,'costs':round(cost,2),'pnl':round(pnl,2)})
                cash_committed-=p['val']; positions.remove(p)
        # ---- entries decided at close of d, filled at open of nxt ----
        if d not in S.index: continue
        t_min=thr.get(d,11)
        cands=[]
        for sym in S.columns:
            if any(p['sym']==sym for p in positions): continue
            sc=S.at[d,sym] if sym in S.columns else np.nan
            df=panels[sym]
            if pd.isna(sc) or d not in df.index: continue
            row=df.loc[d]
            mom=sc>=t_min; mr=bool(row.mr)
            if not(mom or mr): continue
            if pd.isna(row.atr) or row.atr<=0: continue
            cands.append((sc+(0.5 if mr and not mom else 0),sym,'MR' if (mr and not mom) else 'MOM',row))
        cands.sort(reverse=True,key=lambda x:x[0])
        sec_open={}
        for p in positions: sec_open[meta[p['sym']]['sector']]=sec_open.get(meta[p['sym']]['sector'],0)+1
        for sc,sym,strat,row in cands:
            if len(positions)>=MAX_POS: break
            sec=meta[sym]['sector']
            if sec_open.get(sec,0)>=sector_cap: continue
            df=panels[sym]
            if nxt not in df.index: continue
            o=df.at[nxt,'Open']; cl=row.Close
            gap=(o-cl)/cl
            if gap>GAP_CHASE or gap<GAP_DOWN: continue
            slip=SLIP[meta[sym]['tier']]; entry=o*(1+slip)
            a=row.atr; sl=entry-2*a; tg=entry+3*a
            qty=int(RISK/(2*a)); qty=min(qty,int(0.02*CAPITAL/(0.20*entry)),int(MAX_ALLOC_PER/entry))
            if qty<1: continue
            val=qty*entry
            if cash_committed+val>ALLOC_CAP: continue
            positions.append({'sym':sym,'strat':strat,'score':float(sc),'entry':round(entry,2),'entry_date':nxt.date(),
                              'sl':round(sl,2),'tg':round(tg,2),'qty':qty,'val':val,'days':0,
                              'regime':int(regime.get(d,0)),'tier':meta[sym]['tier']})
            cash_committed+=val; sec_open[sec]=sec_open.get(sec,0)+1
    return pd.DataFrame(trades)

def stats(T,label):
    if T.empty: return {'label':label,'trades':0}
    w=T[T.pnl>0]; l=T[T.pnl<=0]
    eq=T.sort_values('exit_date').pnl.cumsum()
    peak=eq.cummax(); dd=(eq-peak).min()
    yrs={str(y):round(g.pnl.sum(),0) for y,g in T.groupby(pd.to_datetime(T.exit_date).dt.year)}
    return {'label':label,'trades':len(T),'win_rate':round(len(w)/len(T)*100,1),
            'avg_win':round(w.pnl.mean(),0) if len(w) else 0,'avg_loss':round(l.pnl.mean(),0) if len(l) else 0,
            'expectancy':round(T.pnl.mean(),0),'profit_factor':round(w.pnl.sum()/abs(l.pnl.sum()),2) if len(l) and l.pnl.sum()!=0 else None,
            'total_pnl':round(T.pnl.sum(),0),'total_costs':round(T.costs.sum(),0),
            'max_dd_inr':round(dd,0),'return_pct':round(T.pnl.sum()/CAPITAL*100,1),
            'by_year':yrs,'by_reason':T.groupby('reason').pnl.agg(['count','sum']).round(0).to_dict('index'),
            'by_strat':T.groupby('strat').pnl.agg(['count','sum','mean']).round(1).to_dict('index'),
            'by_regime':T.groupby('regime').pnl.agg(['count','sum','mean']).round(1).to_dict('index')}

T1=run(costs_on=True);  T1.to_csv('/tmp/bt/trades_net.csv',index=False)
T0=run(costs_on=False)
out={'net':stats(T1,'V9 net of costs'),'gross':stats(T0,'V9 gross')}
json.dump(out,open('/tmp/bt/summary.json','w'),indent=1,default=str)
print(json.dumps(out['net'],indent=1,default=str)[:1500])
print('GROSS total:',out['gross']['total_pnl'],'NET total:',out['net']['total_pnl'])
