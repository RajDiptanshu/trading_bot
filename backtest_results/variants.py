import json, pickle, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, sys
sys.path.insert(0,'/sessions/loving-tender-noether/mnt/trading_bot')
exec(open('engine.py').read().split("T1=run(")[0])  # reuse panels,S,regime,thr,run,stats

# raw MR signal frequency
mrdays=sum(int(df.mr.sum()) for df in panels.values())
print('raw MR signal days across universe:', mrdays)

# Variant A: F1 hard veto (CLAUDE.md spec)
f1=(nif.Close>nma50)
thr_v=thr.copy()
S_veto=S.copy()
S_veto[~f1.reindex(S.index).fillna(False)]= -1   # kill all longs when Nifty<50dma
S_orig=S.copy()
S=S_veto; TA=run(costs_on=True); S=S_orig
print('\nA) F1 HARD VETO:', json.dumps({k:stats(TA,'veto')[k] for k in ['trades','win_rate','expectancy','profit_factor','total_pnl','max_dd_inr']},default=str))

# Variant B: fixed threshold 9 (no dynamic raise)
thr_fix=pd.Series(9,index=thr.index); _t=thr.copy()
globals()['thr']=thr_fix; TB=run(costs_on=True); globals()['thr']=_t
print('B) FIXED THR 9   :', json.dumps({k:stats(TB,'fix9')[k] for k in ['trades','win_rate','expectancy','profit_factor','total_pnl','max_dd_inr']},default=str))

# Decile study: weekly, top-5 by score vs momentum-only vs universe (20d fwd, equal weight)
closes=pd.DataFrame({s:panels[s].Close for s in panels})
fwd20=closes.shift(-21)/closes.shift(-1)-1   # enter next day, 20d hold
fridays=[d for d in S.index if d.weekday()==4 and d>=pd.Timestamp('2019-06-03') and d<=S.index[-25]]
rows=[]
mom_rank=R.rank(axis=1,ascending=False)
for d in fridays:
    if d not in S.index or d not in fwd20.index: continue
    sc=S.loc[d].dropna(); f=fwd20.loc[d]
    if sc.empty: continue
    top=sc.nlargest(5).index
    mtop=mom_rank.loc[d].nsmallest(5).index if d in mom_rank.index else []
    rows.append({'d':d,'top14':f[top].mean(),'mom':f[list(mtop)].mean() if len(mtop) else np.nan,'univ':f[sc.index].mean()})
D=pd.DataFrame(rows).dropna()
ann=lambda x: (x.mean()*12.4)*100  # ~12.4 non-overlap 20d periods/yr
print('\nDECILE STUDY (weekly top-5, 20d fwd, %):')
print(' 14-pt top-5 : mean {:.2f}%  hit>0 {:.0f}%  ann≈{:.1f}%'.format(D.top14.mean()*100, (D.top14>0).mean()*100, ann(D.top14)))
print(' 12mo-mom top: mean {:.2f}%  hit>0 {:.0f}%  ann≈{:.1f}%'.format(D.mom.mean()*100,(D.mom>0).mean()*100, ann(D.mom)))
print(' universe    : mean {:.2f}%  hit>0 {:.0f}%  ann≈{:.1f}%'.format(D.univ.mean()*100,(D.univ>0).mean()*100, ann(D.univ)))
print(' edge of 14pt vs universe: {:.2f}%/20d ({:.0f}% of weeks positive)'.format((D.top14-D.univ).mean()*100, ((D.top14-D.univ)>0).mean()*100))
print(' edge of 14pt vs momentum: {:.2f}%/20d'.format((D.top14-D.mom).mean()*100))
TA.to_csv('trades_veto.csv',index=False)
