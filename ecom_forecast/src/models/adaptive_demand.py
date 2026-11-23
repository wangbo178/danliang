"""实验性非树需求模型；尚未接入生产。组合既有方法，不宣称理论原创。"""
from __future__ import annotations
import numpy as np
import pandas as pd


def profile_history(history, observed=None):
    """分类只看截止日之前数据；统计最近180天，先前未出现部分不算无单。"""
    history = np.asarray(history, float)
    n = history.shape[1]
    first = np.argmax(history > 0, axis=0)
    ever = (history > 0).any(axis=0)
    age = np.where(ever, len(history)-first, 0)
    recent = history[-180:]
    active = np.arange(len(history)-len(recent), len(history))[:, None] >= first[None, :]
    active &= ever[None, :]
    count = active.sum(axis=0)
    mean = (recent*active).sum(axis=0)/np.maximum(count, 1)
    std = np.sqrt((np.square(recent-mean)*active).sum(axis=0)/np.maximum(count, 1))
    cv = std/np.maximum(mean, 1)
    zeros = ((recent == 0) & active).sum(axis=0)/np.maximum(count, 1)
    week = min(len(history)//7, 8)
    weekly = history[-week*7:].reshape(week, 7, n) if week else np.zeros((1, 7, n))
    weekday_level = np.median(weekly, axis=0)
    residual = weekly-weekday_level
    robust_residual = 1.4826*np.median(np.abs(residual), axis=(0,1))/np.maximum(history[-56:].mean(axis=0), 1)
    recent_mean = history[-28:].mean(axis=0)
    previous_mean = history[-56:-28].mean(axis=0) if len(history) >= 56 else recent_mean
    ratio = (recent_mean+1)/(previous_mean+1)
    tags = np.full(n, "volatile", dtype="U20")
    stable = (zeros <= .10) & (robust_residual <= .40) & (ratio >= .67) & (ratio <= 1.5) & (recent_mean >= 10)
    tags[stable] = "stable"
    tags[(zeros >= .35) | (mean < 3)] = "intermittent"
    tags[(ratio < .5) | (ratio > 2)] = "regime_change"
    tags[(age < 90) | (~ever)] = "cold_start"
    tags[(history[-60:].sum(axis=0) == 0) & (age >= 90)] = "dormant"
    return {"tag": tags, "age": age, "mean180": mean, "cv180": cv, "zero_share": zeros,
            "robust_residual_cv": robust_residual, "level_ratio28": ratio, "orders180": recent.sum(axis=0)}


def calendar_design(dates):
    """日历为代码中的确定性已知特征；没有营销预算或历史排期的事后信息。"""
    from config.campaign_calendar import CAMPAIGN_CALENDAR
    from src.features.time_features import build_cn_holiday_features
    dates = pd.DatetimeIndex(dates)
    day = np.arange(len(dates), dtype=float)
    columns = [np.ones(len(dates)), day/365]
    for dow in range(1, 7):
        columns.append((dates.dayofweek == dow).astype(float))
    for harmonic in (1, 2, 3):
        columns.extend([np.sin(2*np.pi*harmonic*dates.dayofyear/365.25), np.cos(2*np.pi*harmonic*dates.dayofyear/365.25)])
    columns += [(dates.day <= 3).astype(float), (dates.day >= dates.days_in_month-2).astype(float)]
    codes = {"38": 1, "618": 2, "1111": 3, "1212": 4}
    phases = np.zeros(len(dates), dtype=int)
    for year, campaigns in CAMPAIGN_CALENDAR.items():
        for name, event in campaigns.items():
            if name not in codes:
                continue
            start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
            phase = np.zeros(len(dates), dtype=int)
            presale = event.get("presale_start")
            if presale:
                phase[(dates >= pd.Timestamp(presale)) & (dates < start)] = 1
            phase[(dates >= start) & (dates <= end)] = 3
            phase[(dates >= start) & (dates < start+pd.Timedelta(days=2))] = 2
            phase[(dates >= end-pd.Timedelta(days=2)) & (dates <= end)] = 4
            phase[(dates > end) & (dates <= end+pd.Timedelta(days=7))] = 5
            present = phase > 0
            phases[present] = codes[name]*10+phase[present]
    for code in codes.values():
        for phase in (1,2,3,4,5):
            columns.append((phases == code*10+phase).astype(float))
    holidays = build_cn_holiday_features(pd.DataFrame({"date": dates}))
    for name in ("is_spring_festival", "is_national_holiday", "is_labor_day", "is_qingming_holiday", "is_duanwu_holiday", "is_zhongqiu_holiday"):
        columns.append(holidays[name].to_numpy(float))
    phases[holidays["is_spring_festival"].to_numpy(bool)] = 60
    return np.column_stack(columns), phases


def candidate_forecasts(history, history_dates, future_dates, design=None, phases=None):
    history = np.asarray(history, float)
    if history.ndim != 2 or len(history) < 56:
        raise ValueError("history 必须是至少56天的二维日序列；冷启动列可在此前填0。")
    if not np.isfinite(history).all() or (history < 0).any():
        raise ValueError("history 必须是有限、非负的日需求；缺失值需先按数据口径处理。")
    future_dates = pd.DatetimeIndex(future_dates)
    h, n = len(future_dates), history.shape[1]
    mean7, mean28 = history[-7:].mean(axis=0), history[-28:].mean(axis=0)
    results = {"mean7": np.tile(mean7, (h,1)), "mean28": np.tile(mean28,(h,1)), "zero": np.zeros((h,n))}
    results["seasonal_naive7"] = history[-7:][np.arange(h)%7].copy()
    weeks = history[-56:].reshape(8,7,n)
    # 四/八周同星期加权，截断过去样本尾部，控制单场爆量对普通日的影响。
    recent_weekdays = np.einsum('w,wdn->dn', np.array([.05,.05,.07,.08,.10,.15,.20,.30]), weeks)
    med = np.median(weeks, axis=0)
    mad = np.median(np.abs(weeks-med), axis=0)
    robust = np.clip(weeks, np.maximum(0,med-3*mad-1), med+3*mad+1)
    robust_weekdays = np.einsum('w,wdn->dn', np.array([.05,.05,.07,.08,.10,.15,.20,.30]), robust)
    results["weekday8"] = recent_weekdays[np.arange(h)%7]
    trend = np.clip((mean7+1)/(mean28+1), .75, 1.30)
    results["robust_weekday"] = robust_weekdays[np.arange(h)%7] * trend[None,:]**np.exp(-np.arange(h)[:,None]/14)

    level = history[-180:][0].copy()
    slope = np.zeros(n)
    variance = np.square(np.maximum(level,1))
    size = np.zeros(n)
    probability = np.zeros(n)
    interval = np.ones(n)
    age_since = np.ones(n)
    started = np.zeros(n,dtype=bool)
    for row in history[-365:]:
        positive = row > 0
        first = positive & ~started
        size[first], interval[first], probability[first] = row[first], 1, .10
        update = positive & started
        size[update] += .10*(row[update]-size[update])
        interval[update] += .10*(age_since[update]-interval[update])
        probability[started] += .08*(positive[started].astype(float)-probability[started])
        started |= positive
        age_since = np.where(positive,1,age_since+1)
    results["tsb"] = np.tile(size*probability,(h,1))
    results["croston_sba"] = np.tile(.95*size/np.maximum(interval,1),(h,1))
    level = history[-180:][0].copy()
    for row in history[-180:]:
        innovation = row-(level+.95*slope)
        bound = 3*np.sqrt(variance)+5
        clean = np.clip(innovation,-bound,bound)
        next_level = np.maximum(0,level+.95*slope+.18*clean)
        slope = .04*(next_level-level)+.96*.95*slope
        variance = .95*variance+.05*np.square(innovation)
        level = next_level
    damped_steps = np.cumsum(.95**np.arange(1,h+1))[:,None]
    results["damped_level"] = np.maximum(0,level[None,:]+damped_steps*slope[None,:])
    weights = np.exp(-np.arange(min(90,len(history))-1,-1,-1)/14)
    ewma = np.einsum('t,tn->n',weights/weights.sum(),history[-len(weights):])
    results["ewma14"] = np.tile(ewma,(h,1))

    all_dates = pd.DatetimeIndex(history_dates).append(future_dates)
    if design is None:
        design, phases = calendar_design(all_dates)
    xt = design[len(history):len(history)+h].copy()
    first = np.argmax(history>0,axis=0)
    ridge_prediction = np.zeros((h,n))
    for j in range(n):
        a = max(int(first[j]),len(history)-730)
        if len(history)-a < 90:
            ridge_prediction[:,j] = mean28[j]
            continue
        x = design[a:len(history)].copy()
        target = history[a:,j]
        scale = max(float(mean28[j]),1)
        w = np.exp(-np.arange(len(target)-1,-1,-1)/180)
        penalty = np.eye(x.shape[1])*8
        penalty[0,0] = .05
        # 斜率以起报日为零，降低绝对日期数值带来的惩罚偏移。
        x[:,1] -= design[len(history)-1,1]
        future_x = xt.copy()
        future_x[:,1] -= design[len(history)-1,1]
        coef = np.linalg.solve(x.T@(w[:,None]*x)+penalty, x.T@(w*target/scale))
        fitted_recent = np.maximum(0,x[-28:]@coef*scale)
        adjustment = np.clip((target[-28:].sum()+10)/(fitted_recent.sum()+10),.7,1.4)
        ridge_prediction[:,j] = np.maximum(0,future_x@coef*scale)*adjustment
    results["calendar_ridge"] = ridge_prediction

    historical_phase = phases[:len(history)]
    baseline = pd.DataFrame(history).rolling(28,min_periods=7).mean().shift(1).to_numpy()
    ratios = np.divide(history, baseline+1)
    analogue = results["weekday8"].copy()
    for phase in np.unique(phases[len(history):len(history)+h]):
        if phase == 0:
            continue
        ix = np.flatnonzero(historical_phase == phase)
        ix = ix[ix >= max(0,len(history)-1095)]
        if len(ix) < 3:
            continue
        factor = np.nanmedian(ratios[ix],axis=0)
        factor = 1+len(ix)/(len(ix)+14)*(np.clip(factor,.2,6)-1)
        analogue[phases[len(history):len(history)+h] == phase] *= np.nan_to_num(factor,nan=1)
    results["calendar_analogue"] = analogue
    known = (history>0).any(axis=0)
    for name in results:
        results[name] = np.clip(np.nan_to_num(results[name]),0,None)
        results[name][:,~known] = 0
    return results


def adaptive_combination(forecasts, validations, history, keys):
    """仅用起报日前的独立验证结果选择专家，并向平台×渠道同行误差收缩。"""
    names = [name for name in forecasts if name != "zero"]
    n = history.shape[1]
    errors = np.zeros((len(names),n))
    bias = np.zeros_like(errors)
    volumes = np.zeros(n)
    for actual, predictions in validations:
        volumes += actual.sum(axis=0)
        for k,name in enumerate(names):
            errors[k] += np.abs(actual-predictions[name]).sum(axis=0)
            bias[k] += (predictions[name]-actual).sum(axis=0)
    local = (errors+.15*np.abs(bias))/(volumes[None,:]+1)
    grouped = np.zeros_like(local)
    for platform,channel in sorted(set((row[1],row[2]) for row in keys)):
        mask = (keys[:,1]==platform)&(keys[:,2]==channel)
        denominator = max(volumes[mask].sum(),1)
        grouped[:,mask] = ((errors[:,mask].sum(axis=1)+.15*np.abs(bias[:,mask].sum(axis=1)))/denominator)[:,None]
    reliability = volumes/(volumes+2000)
    score = reliability[None,:]*local+(1-reliability[None,:])*grouped
    profile = profile_history(history)
    for j,tag in enumerate(profile["tag"]):
        if tag == "stable":
            allowed = {"mean7","mean28","weekday8","robust_weekday","damped_level","ewma14","calendar_ridge","calendar_analogue"}
        elif tag in {"intermittent","dormant","cold_start"}:
            allowed = {"mean7","mean28","tsb","croston_sba","ewma14","calendar_ridge"}
        else:
            allowed = set(names)
        for k,name in enumerate(names):
            if name not in allowed:
                score[k,j] = np.inf
    # 最多混合前三名，权重全由更早验证期确定。
    chosen = np.argsort(score,axis=0)[:3]
    weights = np.zeros_like(score)
    best = np.min(score,axis=0)
    for j in range(n):
        logits = -(score[chosen[:,j],j]-best[j])/.15
        values = np.exp(np.clip(logits,-50,0))
        weights[chosen[:,j],j] = values/values.sum()
    result = sum(weights[k][None,:]*forecasts[name] for k,name in enumerate(names))
    dormant = profile["tag"] == "dormant"
    result[:,dormant] = 0
    return result, {"names": names, "weights": weights, "scores": score, "profile": profile}
