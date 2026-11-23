"""生成可复算的分渠道评估 JSON 与自包含 HTML；不改写配置或生产模型。"""
import base64
import html
import json
import hashlib
from importlib.metadata import version
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from research_common import ROOT,OUT,ORIGINS,HORIZON,load_snapshot,eligible_mask,metrics

LABELS = {"mean7":"近7日均值","mean28":"近28日均值","seasonal_naive7":"上周同日重复",
          "weekday8":"8周同星期加权","robust_weekday":"稳健星期模型","ewma14":"指数平滑",
          "damped_level":"稳健阻尼趋势","tsb":"TSB发生概率×单量","croston_sba":"Croston-SBA",
          "calendar_ridge":"日历岭回归","calendar_analogue":"历史活动相似期",
          "adaptive_non_tree":"自适应分型组合（实验）","zero":"全零参考",
          "current":"现有实现（含串平台缺陷）","identity":"现有算法＋平台键隔离对照",
          "current_lifecycle":"现有实现＋休眠归零后处理","identity_lifecycle":"平台键隔离＋休眠归零后处理"}
TAGS = {"stable":"候选稳定","volatile":"持续波动","regime_change":"近期量级切换（含大促）",
        "intermittent":"间歇/低量","cold_start":"冷启动","dormant":"近期休眠"}
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def number(value):
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:,.2f}"


def table(headers,rows):
    return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+html.escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def image_tag(path):
    return '<img src="data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()+'" alt="评估图表">'


if __name__ == "__main__":
    partial = "--partial" in sys.argv
    y,observed,dates,keys,meta = load_snapshot()
    included = eligible_mask(keys,meta["config"])
    profiles = json.loads((OUT/"series_profiles.json").read_text(encoding="utf-8"))
    quality = json.loads((OUT/"source_quality.json").read_text(encoding="utf-8"))
    folds,all_predictions,actuals,observations,tags,fold_info = [],{},[],[],[],[]
    for origin in ORIGINS:
        expected = [OUT/f"{prefix}_{origin}.npz" for prefix in ("current","identity","non_tree")]
        if not all(path.exists() for path in expected):
            if partial:
                continue
            raise RuntimeError("回测未完成："+origin)
        cutoff = dates.get_loc(origin)
        predictions = dict(np.load(expected[2]))
        for prefix,path in zip(("current","identity"),expected[:2]):
            result = np.load(path)
            predictions[prefix] = result["current"].copy()
            predictions[prefix+"_lifecycle"] = result["current"].copy()
            predictions[prefix+"_lifecycle"][:, y[cutoff-59:cutoff+1].sum(axis=0)==0] = 0
        actual = y[cutoff+1:cutoff+31]
        actuals.append(actual)
        observations.append(observed[cutoff+1:cutoff+31])
        tags.append(np.load(OUT/f"selection_{origin}.npz")["tags"])
        fold_info.append({"origin":origin,"test_start":str(dates[cutoff+1].date()),"test_end":str(dates[cutoff+30].date())})
        for name,prediction in predictions.items():
            all_predictions.setdefault(name,[]).append(prediction)
            folds.append({"origin":origin,"algorithm":name,"horizon":30,**metrics(actual[:,included],prediction[:,included])})
    if not actuals:
        raise RuntimeError("尚无完成的公共回测窗口")
    actual = np.concatenate(actuals)
    observed_score = np.concatenate(observations)
    prediction_arrays = {name:np.concatenate(value) for name,value in all_predictions.items()}
    overall = []
    for name,prediction in prediction_arrays.items():
        row = {"algorithm":name,**metrics(actual[:,included],prediction[:,included])}
        row["company_daily_wape"] = metrics(actual[:,included].sum(axis=1),prediction[:,included].sum(axis=1))["wape"]
        obs = observed_score[:,included]
        row["observed_rows_only_wape"] = metrics(actual[:,included][obs],prediction[:,included][obs])["wape"]
        a7 = np.concatenate([a[:7,included] for a in actuals])
        p7 = np.concatenate([p[:7,included] for p in all_predictions[name]])
        row["horizon7_wape"] = metrics(a7,p7)["wape"]
        overall.append(row)
    overall.sort(key=lambda row:row["wape"])
    channel_rows = []
    for platform,channel in sorted(set((str(row[1]),str(row[2])) for row in keys)):
        mask = (keys[:,1]==platform)&(keys[:,2]==channel)
        recent = y[-180:,mask].sum()
        if recent <= 0:
            continue
        indices = np.flatnonzero(mask)
        stable_volume = sum(profiles[i]["orders180"] for i in indices if profiles[i]["tag"]=="stable")
        changed_volume = sum(profiles[i]["orders180"] for i in indices if profiles[i]["tag"]=="regime_change")
        methods = {name:metrics(actual[:,mask],prediction[:,mask]) for name,prediction in prediction_arrays.items()}
        best = min((name for name in methods if name not in {"zero","current","current_lifecycle"}), key=lambda name: methods[name]["wape"] if methods[name]["wape"] is not None else float("inf"))
        channel_rows.append({"platform":platform,"channel":channel,"series":int(mask.sum()),
                             "included":bool(included[mask].all()),"orders180":float(recent),
                             "stable_volume_share":stable_volume/recent,"changed_volume_share":changed_volume/recent,
                             "best_ex_post":best,"metrics":methods})
    channel_rows.sort(key=lambda row:-row["orders180"])
    tag_metrics = []
    for tag in TAGS:
        a_parts,p_parts = [],{name:[] for name in prediction_arrays}
        for fold_index,fold_tag in enumerate(tags):
            mask = included & (fold_tag==tag)
            a_parts.append(actuals[fold_index][:,mask].ravel())
            for name in prediction_arrays:
                p_parts[name].append(all_predictions[name][fold_index][:,mask].ravel())
        actual_tag = np.concatenate(a_parts)
        tag_metrics.append({"tag":tag,"actual":float(actual_tag.sum()),
                            "methods":{name:metrics(actual_tag,np.concatenate(values)) for name,values in p_parts.items()}})
    current_tags = []
    for tag in TAGS:
        selection = [row for row in profiles if row["tag"]==tag and row["included"]]
        current_tags.append({"tag":tag,"series":len(selection),"orders180":sum(row["orders180"] for row in selection)})
    active_count = sum(row["orders180"]>0 and row["included"] for row in profiles)
    source_total = meta["total_orders"]
    warehouse_total = float(meta["warehouse"][0]["orders"])
    result = {"protocol":{"origins":fold_info,"horizon":30,"inner_windows":[90,60,30],
                          "first_day":meta["date_start"],"last_day":meta["date_end"],
                          "series":len(keys),"eligible_series":int(included.sum()),"active_series180":active_count,
                          "missing_days":meta["global_missing_dates"],"snapshot_sha256":meta["snapshot_sha256"],
                          "calendar_vintages_available":False,"future_truth_used_for_fitting":False,
                          "auxiliary_inputs":"共同历史订单与已知日历；不使用无历史版本的排期、未来GMV、观看量或佣金。",
                          "zero_fill":meta["zero_fill_assumption"]},
              "overall":overall,"folds":folds,"channels":channel_rows,"regime_metrics":tag_metrics,
              "current_regimes":current_tags,"source_quality":quality,
              "source_warehouse_total_difference":source_total-warehouse_total,
              "source_warehouse_total_difference_pct":(source_total-warehouse_total)/source_total*100}
    result["environment"] = {name:version(name) for name in ("numpy","pandas","lightgbm","scikit-learn","matplotlib")}
    result["implementation_sha256"] = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT/"ecom_forecast/src/models/channel_models.py",ROOT/"ecom_forecast/src/features/time_features.py",
                     ROOT/"ecom_forecast/src/pipeline/forecast_pipeline.py",ROOT/"ecom_forecast/src/models/adaptive_demand.py")}
    (OUT/"evaluation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    np.savez_compressed(OUT/"evaluation_predictions.npz",actual=actual,**prediction_arrays)

    # 标准静态科研图：预测误差、窗口变化与总量轨迹。
    visible = [row for row in overall if row["algorithm"] not in {"current","current_lifecycle","zero"}]
    fig,ax = plt.subplots(figsize=(12,7))
    ax.barh([LABELS[row["algorithm"]] for row in visible][::-1],[row["wape"] for row in visible][::-1],color=["#2563eb" if row["algorithm"]=="adaptive_non_tree" else "#94a3b8" for row in visible][::-1])
    ax.set_xlabel("店铺×平台×渠道×日 WAPE（%，越低越好）")
    ax.set_title(f"统一固定起点30天预测 · {len(fold_info)}个公共窗口 · 排除999作废单")
    ax.grid(axis="x",alpha=.2)
    fig.tight_layout();fig.savefig(OUT/"algorithm_comparison.png",dpi=160);plt.close(fig)
    lines = ["identity","adaptive_non_tree","robust_weekday","mean28"]
    fig,axes = plt.subplots(2,1,figsize=(12,9),sharex=True)
    for name in lines:
        values = [next(row for row in folds if row["origin"]==fold["origin"] and row["algorithm"]==name) for fold in fold_info]
        axes[0].plot(range(len(fold_info)),[row["wape"] for row in values],marker="o",label=LABELS[name])
        axes[1].plot(range(len(fold_info)),[row["bias_pct"] for row in values],marker="o",label=LABELS[name])
    axes[0].set_ylabel("WAPE %");axes[0].legend(fontsize=9);axes[0].set_title("不同业务窗口的稳定性：普通月份与大促不能合并解释")
    axes[1].set_ylabel("Bias %（正为高估）");axes[1].axhline(0,color="black",linewidth=.8)
    axes[1].set_xticks(range(len(fold_info)),[row["test_start"]+"\n至"+row["test_end"] for row in fold_info],fontsize=8)
    for ax in axes:ax.grid(alpha=.2)
    fig.tight_layout();fig.savefig(OUT/"window_comparison.png",dpi=160);plt.close(fig)
    fig,ax = plt.subplots(figsize=(12,5))
    ax.plot(actuals[-1][:,included].sum(axis=1),label="实际有效订单",color="#111827",linewidth=2)
    for name in lines[:3]:
        ax.plot(all_predictions[name][-1][:,included].sum(axis=1),label=LABELS[name],alpha=.85)
    ax.set_xlabel("最后一个窗口内的预测日（第0日为"+fold_info[-1]["test_start"]+"）")
    ax.set_ylabel("日订单量");ax.legend(fontsize=9);ax.set_title("最新30天公共窗口：每日总量轨迹（汇总可能抵消店铺误差）");ax.grid(alpha=.2)
    fig.tight_layout();fig.savefig(OUT/"latest_daily.png",dpi=160);plt.close(fig)

    overall_table = table(["算法","固定30天 WAPE %","固定7天 WAPE %","30天 Bias %","公司日汇总 WAPE %","仅有源记录行 WAPE %"],
                          [[LABELS[row["algorithm"]],number(row["wape"]),number(row["horizon7_wape"]),number(row["bias_pct"]),number(row["company_daily_wape"]),number(row["observed_rows_only_wape"])] for row in overall])
    channel_table = table(["平台","渠道","纳入当前测算","近180天单量","稳定序列单量占比","量级切换占比","实验组合 WAPE","隔离平台后的树模型 WAPE","测试集事后最优（不可当预测路由）"],
        [[row["platform"],row["channel"],"是" if row["included"] else "否（保持人工配置）",f'{row["orders180"]:,.0f}',f'{row["stable_volume_share"]*100:.1f}%',f'{row["changed_volume_share"]*100:.1f}%',
          number(row["metrics"]["adaptive_non_tree"]["wape"]) if row["included"] else "未评分",number(row["metrics"]["identity"]["wape"]) if row["included"] else "未评分",LABELS[row["best_ex_post"]] if row["included"] else "不自动启用"] for row in channel_rows[:30]])
    stable = sorted([row for row in profiles if row["tag"]=="stable"],key=lambda row:-row["orders180"])
    stable_table = table(["店铺","平台","渠道","近180天单量","稳健残差变异系数","近28天/前28天水平","近180天零单比例"],
        [[row["shop_id"],row["platform"],row["channel"],f'{row["orders180"]:,.0f}',number(row["robust_residual_cv"]),number(row["level_ratio28"]),f'{row["zero_share"]*100:.1f}%'] for row in stable[:25]])
    regime_table = table(["起报时分型","测试实际单量","实验组合 WAPE","隔离平台后的树模型 WAPE","稳健星期 WAPE"],
        [[TAGS[row["tag"]],f'{row["actual"]:,.0f}',number(row["methods"]["adaptive_non_tree"]["wape"]),number(row["methods"]["identity"]["wape"]),number(row["methods"]["robust_weekday"]["wape"])] for row in tag_metrics])
    all_series_table = table(["店铺","平台","渠道","当前分型","近180天单量","变异系数","零单比例","水平变化","纳入当前测算"],
        [[row["shop_id"],row["platform"],row["channel"],TAGS[row["tag"]],f'{row["orders180"]:,.0f}',number(row["cv180"]),f'{row["zero_share"]*100:.1f}%',number(row["level_ratio28"]),"是" if row["included"] else "否"] for row in sorted(profiles,key=lambda row:-row["orders180"])])
    best = next(row for row in overall if row["algorithm"] not in {"current","current_lifecycle","zero"})
    adaptive = next(row for row in overall if row["algorithm"]=="adaptive_non_tree")
    identity = next(row for row in overall if row["algorithm"]=="identity")
    zero_pct = float(quality["live"]["zero_order_rows"])/float(quality["live"]["records"])*100
    counts_text = "；".join(f'{TAGS[row["tag"]]} {row["series"]} 个' for row in current_tags)
    body = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>渠道预测算法与计算逻辑评估｜2026-10-29</title>
    <style>body{{font:15px/1.8 'Microsoft YaHei',sans-serif;color:#263447;background:#f3f6fa;margin:0}}main{{max-width:1240px;margin:30px auto;background:white;padding:38px;border-radius:16px}}h1{{font-size:30px;line-height:1.4}}h2{{margin-top:40px;color:#143a65}}h3{{margin-top:26px}}p{{max-width:1100px}}.note{{background:#eff6ff;border-left:4px solid #2563eb;padding:15px 20px;margin:18px 0}}.risk{{background:#fff7ed;border-color:#f97316}}table{{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}}th,td{{padding:9px 12px;border-bottom:1px solid #e2e8f0;text-align:right}}th{{background:#edf2f8;position:sticky;top:0}}td:first-child,th:first-child{{text-align:left}}.table-wrap{{overflow:auto;max-height:740px;margin:18px 0}}img{{max-width:100%;height:auto}}code{{background:#f1f5f9;padding:2px 5px}}li{{margin-bottom:8px}}a{{color:#2563eb}}small{{color:#64748b}}@media(max-width:800px){{main{{margin:0;padding:18px}}}}</style><main>
    <h1>渠道预测算法与计算逻辑评估</h1><p><strong>数据快照：{meta['read_at']}</strong> · 2023-01-01 至 2026-10-28 · 排除 999 作废单 · 数据库全程只读</p>
    <div class="note"><strong>结论：有必要重构计算键、评估和模型路由；目前没有证据支持单纯换一种算法就能解决问题。</strong><br>
    现有实现存在跨平台串序列问题。隔离平台后仍需分别处理稳定需求、活动冲击、间歇需求与量级切换。
    自主实现的非树组合 WAPE 为 {adaptive['wape']:.2f}%，隔离平台后的树模型为 {identity['wape']:.2f}%；本次测试事后最优为「{LABELS[best['algorithm']]}」{best['wape']:.2f}%。
    是否上线应看新时期影子验证，而不能把最小测试误差直接作为上线承诺。所有生产模型文件与人工渠道配置保持原样。</div>

    <h2>1. 数据覆盖与不能忽略的口径</h2>
    <p>快照包含 {meta['rows']:,} 行有效日汇总、{len(keys)} 个店铺×平台×渠道序列，总有效订单 {source_total:,.0f} 单。
    当前人工配置排除部分渠道，统一比较的主样本为 {int(included.sum())} 个序列；其中近180天有需求的为 {active_count} 个。被排除渠道仅作描述，不自动启用或改动设置。</p>
    <p>{counts_text}。这些标签是起报时可计算的筛选规则，近期量级切换可能来自大促，也可能来自运营或店铺切换，不代表已经识别因果。</p>
    <ul><li>全局日历没有整日缺失。对汇总表内部未出现组合，研究按“首次出现之后、已完整采集日期上的无单”补0；上游仍应确认这一约定。为便于核查，另列只在源表有记录行上评分的结果。</li>
    <li>24家店铺曾跨平台。不能只用店铺编码和渠道名作为时序键，也不能把平台差异丢掉后再分别输出。</li>
    <li>仓库表只有付款日×库存地点，全量为 {warehouse_total:,.0f} 单，与渠道有效订单相差 {source_total-warehouse_total:,.0f} 单（{(source_total-warehouse_total)/source_total*100:.2f}%）。差异原因不能凭现有汇总表判定，分仓前需要对齐统计范围；当前只能学整体仓占比，不能验证店铺专属分仓。</li>
    <li>主播特征表 {int(quality['live']['records']):,} 行中 {zero_pct:.2f}% 为零成交记录；这是零膨胀需求，不能简单删零后按成交均值推所有场次。昵称 {int(quality['live']['nicknames']):,} 个、账号 {int(quality['live']['author_ids']):,} 个，建议账号为主键、昵称为别名。</li>
    <li>本地排期只有 {quality['schedule']['records']} 行、覆盖 {quality['schedule']['shops']} 家店，{quality['schedule']['missing_channel']} 行缺渠道，未来排期 {quality['schedule']['future_rows']} 行；佣金数据为0行。当前信息不足以可靠估计所有渠道的未来活动和直播峰值。</li></ul>

    <h2>2. 如何做这次评估</h2>
    <p>六个不重叠的30天测试窗口覆盖双11、春节、618及普通月份。起报之后不喂入任何一天的真实销量，模拟一次性生成未来30天计划；7天指标取同一起报点的前7天。
    模型及组合权重只从更早数据学习；实验组合使用起报日前90、60、30天开始的三个内层窗口。
    这一流程遵循<a href="https://otexts.com/fpp3/toolbox.html#time-series-cross-validation">滚动起点时间序列验证</a>，并特别区分逐日更新真实值和固定起点多步预测。</p>
    {table(['起报日','测试开始','测试结束'],[[row['origin'],row['test_start'],row['test_end']] for row in fold_info])}
    <p>主指标 WAPE = 所有店铺×平台×渠道×日的绝对误差合计 ÷ 实际订单合计；Bias 为累计高估/低估比例。公司日汇总 WAPE 会抵消店铺间误差，不能替代主指标。
    WAPE 不是分类准确率，可能超过100%；不能简单宣称“准确率=100%-WAPE”。未出现过的新序列预测为0，其实际需求仍计入误差。</p>
    <p>为保证输入公平，所有模型只使用共同的历史订单与确定性日历，未使用缺少历史版本的飞书排期、未来观看量、GMV或佣金。
    现有模型按当前默认轮数和特征重新训练，仅将计算线程限制为4；平台隔离对照只把店铺键变为“店铺@@平台”，保持算法不变。
    现有实现的 raw/guardrail 数值来自同一条递归路径，未冒充独立算法纳入排名。</p>
    <div class="note risk">这是回溯快照评估，不是在线A/B：源表历史订单状态可能晚于当时起报日才最终修订；历史日历与排期也没有完整版本。
    参数设计和误差窗口有限，事后最优及分渠道最优有选择偏差，不是对未来的无偏保证。下一步需新时期的冻结版本影子预测。</div>

    <h2>3. 算法比较与准确性</h2>{overall_table}
    <p>休眠归零为“起报前60天均无单的序列，输出归零”的后处理对照，绝不根据未来销量决定。它只是诊断生命周期影响，不代表已恢复全部模型正确性。
    全零预测仅是误差参考，不参与主动序列的实验模型选择。</p>
    {image_tag(OUT/'algorithm_comparison.png')}{image_tag(OUT/'window_comparison.png')}{image_tag(OUT/'latest_daily.png')}
    <h3>按起报时分型评分</h3>{regime_table}

    <h2>4. 哪些渠道更适合稳定模型</h2>
    <p>不按名称认定稳定。候选稳定要求最近180天零单比例≤10%，最近8周同星期稳健残差变异系数≤0.40，近28天/前28天水平在0.67～1.50之间，近28天日均≥10单，且有至少90天历史。
    优先考虑稳健星期、指数平滑或阻尼水平模型，再由较早验证窗口选型。近期量级切换和大促型应单独看，不能因名字是“店铺”“其他”就归稳定。
    稳定标签描述起报时的近期历史；未来若进入已知大促，也应切换到活动情景，而不是把稳定模型全年固定使用。</p>
    <h3>当前候选稳定的具体店铺渠道</h3>{stable_table}
    <h3>平台×渠道汇总</h3><p>稳定占比为该渠道内候选稳定序列的近180天单量占比；同一渠道不能据此无条件全量套一个算法。</p>{channel_table}

    <h2>5. 需要重构的计算逻辑，按优先级排序</h2>
    <ol><li><strong>P0：统一序列键。</strong>当前 <code>channel_models.py:_prepare_features</code> 与缺失特征回填多次只按 <code>shop_id,channel</code> 聚合。
    首个窗口中 CS07 的 TM/店铺、TB/店铺、GUANWANG/店铺被预测出相同的约796,436单，而实际分别约880,827、56、0单。
    同组跨平台预测又被写回递归历史，可能继续倍增。直连改造后的跨平台数据使这一旧假设不再成立。应将平台纳入所有滞后、滚动、同比、回填、编码、校准及事件关联键，并增加守恒和隔离回归测试。</li>
    <li><strong>P0：拆开评估与选择。</strong><code>apply_channel_calibration_guardrail</code> 使用当前测试期真实误差决定是否回退，而该期又用于报告指标。
    生产可把近期数据作为验证来决定下一期模型，但不能同时把它叫独立测试。本报告没有调用这一测试期回退选择。</li>
    <li><strong>P1：按序列动态分型，保持业务类别独立。</strong>当前 <code>channel_type_map</code> 仅以渠道名为键。TM/达播与TB/达播、不同平台的CPS等存在配置冲突；未知名称又默认stable。
    业务“达人/自营/联盟”应管理数据解释，需求“稳定/变化/间歇”应管理算法分配；用组合键和训练期统计路由，保留人工覆盖。</li>
    <li><strong>P1：建立真实日历面板与生命周期。</strong>源表只返回有汇总的日期，而部分滚动均值仍是最近N行，缺日时不是N天。把0单、漏采、未开店、已停运分别标记。
    在缺采口径未明确之前，应保留报告中的敏感性评分，不能把缺行全部当作确定的0。</li>
    <li><strong>P1：直播增量口径一致。</strong>当前源主播实绩没有系统店铺键，无法从某店历史标签可靠扣除；未来排期却仍能把主播预测加到渠道基础量上，存在重复解释直播量的风险。
    应选“预测含直播的总单量”或“可识别的无直播基线+明确场次增量”之一，并用主播账号、系统店铺、平台、渠道、场次ID建立可核查关系。关系不明时提供情景区间或人工计划，不把全局主播量强行分店。</li>
    <li><strong>P2：减少无效复杂度。</strong>global模型虽然训练和保存，当前主预测路径主要调用渠道模型，未形成明确的层级校准。
    要么用它做可验证的公司/平台总量约束并协调到店铺，要么从常规流程移除冗余训练；不要仅保留“分三层”的形式。</li></ol>

    <h2>6. 已实现的非树实验模型与建议架构</h2>
    <p><code>src/models/adaptive_demand.py</code> 已实现稳健同星期、指数平滑、阻尼趋势、Croston-SBA、TSB、带节假日与活动项的岭回归及历史活动相似期候选。
    自适应组合按起报时分型限制候选集合，利用过去三个验证窗口的 WAPE 与Bias打分，低样本序列向同平台同渠道误差收缩，再混合前三名。
    这是对成熟方法的自主实现和组合，未证明理论原创；本次实验组合也未必优于简单方法，不以“自研”替代证据。</p>
    <p>TSB针对发生概率和条件单量分别更新，零单期仍下调发生概率，适合间歇或休眠需求；其依据见
    <a href="https://www.sciencedirect.com/science/article/pii/S0377221711004437">Teunter、Syntetos、Babai 的原始论文</a>。
    不把这种方法当作大型直播活动峰值的替代；具体场次仍需计划、流量或目标输入。</p>
    {table(['需求形态','优先候选','决定性补充信息'],[
        ['稳定/周季节','稳健星期、EWMA、阻尼状态模型；与修复后的树模型共同验证','完整日历与店铺生命周期'],
        ['活动/直播峰值','明确无活动基线＋场次/活动增量，或条件总量模型','平台渠道明确的排期、目标、预算、历史版本'],
        ['近期量级切换','短窗自适应水平、变点后的遗忘模型','渠道迁移、价格变化、投放、缺货标记'],
        ['间歇/小单','TSB、Croston-SBA或发生概率×条件单量','不能删除真实零单，也不能把漏采当零单'],
        ['新店/冷启动','同平台同行收缩＋人工初始计划','开店日、投放计划、初始目标'],
        ['分仓','同口径总量约束后按逻辑仓整体份额分配','要到店铺级需补充店铺×库存地点事实']])}
    <p><strong>落地次序：</strong>先修复序列键和评估协议，保留已有业务手工配置；再以影子模式并行输出修复后的树模型与非树候选；
    用未参与本次研究的新窗口决定具体店铺渠道的路由。达到更低WAPE、可接受Bias、峰值及仓级守恒后，才替换生产入口。</p>

    <h2>7. 完整序列清单与复算</h2><p>以下为全部326个序列。可使用浏览器查找店铺编码或渠道名。</p>{all_series_table}
    <p>机器可读结果：<code>evaluation.json</code>；冻结日面板：<code>snapshot.npz</code>；元数据含快照SHA256；
    逐窗口预测文件：<code>current_*.npz / identity_*.npz / non_tree_*.npz</code>。所有文件仅在本地 output/research_20261029。</p>
    <p>复算命令：<code>.venv/Scripts/python.exe script/research_non_tree.py</code>、
    <code>script/research_current_model.py</code>（支持缓存）、<code>script/research_current_model.py --separate-platform</code>、
    <code>script/research_report.py</code>。实验算法行为验证为 <code>script/test_adaptive_demand.py</code>。</p>
    <small>研究结果不等于已部署模型；未改写数据库事实、人工渠道分类、已有预测和生产joblib文件。</small>
    </main></html>"""
    (OUT/"渠道算法评估报告.html").write_text(body,encoding="utf-8")
    print(json.dumps({"windows":len(fold_info),"overall":overall[:7],"report":str(OUT/"渠道算法评估报告.html")},ensure_ascii=False,indent=2),flush=True)
