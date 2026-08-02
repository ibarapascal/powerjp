"""発電所匹配的**空间校验** —— 找出「名字对上了但坐标可能是错的」。

## 为什么必须做

`plant_coords.py` 报的覆盖率只保证**名字归一后相等 + 打分选了个候选**，
不保证选对了。名字对、坐标错的后果是**把 A 的出力静默画到 B 的位置上** ——
不报错、不掉覆盖率，只有盯着地图看的人才可能发现。比不匹配危险得多。

## 四项检查

| 检查 | 原理 | 抓什么 |
|---|---|---|
| **① 消歧没把握** | 冠军与亚军**分差小、距离远** | 打分区分不开两个同名设施，选中的可能是错的 |
| **② 选中分数低** | `score()` 无正向信号 | 纯靠「有坐标」选出来的，等于蒙 |
| **③ 一名多原名** | 事业者消歧后仍合并的 | 剩余的错并（消歧漏网） |
| **④ 区域合理性** | 到 `対象エリア` 参考点的距离 | 跨岛级错配 |

## ⚠️ 检查 ④ 的已知陷阱

**电力区域 ≠ 地理地方**。いわき（福島県）地理属東北、供电属東京電力；
所以「坐标不在该区域地理范围内」**不能**直接判错 —— `FLOW_MATCHING.md`
里就是这么误杀了 4.1pt。这里只用**很宽的阈值**抓跨岛级别，不做硬约束。

## ⚠️ 为什么不做「多源坐标两两互验」

试过，废掉了。同名候选里本来就混着不同实体（`広野火力` 和 `広野小学校太阳能`），
两两算距离会把**正常情况**也报成分歧，噪声淹没信号。
正确的问法不是「各源坐标一致吗」，而是「**选中的这个有把握吗**」。

用法：
    python -m pipeline.structured.plant_verify
    python -m pipeline.structured.plant_verify --gap 15 --km 10
"""

import argparse
import json
import logging
from pathlib import Path

from pipeline.core import fetch, paths, report
from pipeline.structured.plant_coords import (AREA_REF, XREF, build_pool,
                                              haversine_km, load_occto, resolve,
                                              score, valid)

log = logging.getLogger("powerjp.plant_verify")

GAP = 20.0        # 冠亚军分差小于这个算「区分不开」
FAR_KM = 10.0     # 且位置差超过这个 → 可疑
LOW_SCORE = 25.0  # 选中候选的分数低于这个 = 几乎没有正向信号
AREA_FAR_KM = 400.0


def main() -> None:
    ap = argparse.ArgumentParser(description="発電所匹配的空间校验")
    ap.add_argument("--date")
    ap.add_argument("--gap", type=float, default=GAP)
    ap.add_argument("--km", type=float, default=FAR_KM)
    ap.add_argument("--show", type=int, default=20)
    ap.add_argument("--dump", nargs="?", const="", help="落盘校验结果")
    args = ap.parse_args()
    fetch.setup_logging()

    pool = build_pool()
    occ, tot = load_occto(args.date)
    xref = json.loads(XREF.read_text(encoding="utf-8"))
    got, _, _ = resolve(occ, pool, xref)

    shaky, lowsc, far = [], [], []
    for k, chosen in got.items():
        o = occ[k]
        cands = [c for c in pool.get(o["name"], []) if valid(c.get("pt"))]
        ranked = sorted(cands, key=lambda c: score(o, c), reverse=True)
        # ⚠️ 分数要算**实际选中的那个**，不是 pool[name] 的冠军 ——
        #    xref 命中的电站在 pool[name] 下本来就没候选（那正是它走 xref 的原因），
        #    拿空列表算会得到 score=0，把 20 条正常的 xref 全报成「没有正向信号」。
        best = score(o, chosen)

        # ① 冠亚军分差小 + 位置差远 = 打分区分不开（只对 auto 有意义）
        if chosen["how"] == "auto" and len(ranked) >= 2:
            second = score(o, ranked[1])
            d = haversine_km((float(ranked[0]["pt"][0]), float(ranked[0]["pt"][1])),
                             (float(ranked[1]["pt"][0]), float(ranked[1]["pt"][1])))
            if best - second < args.gap and d >= args.km:
                shaky.append((o["kwh"], k, best - second, d, ranked[0], ranked[1]))

        # ② 选中分数低 —— 只看 auto。xref 是人工核对过的，本来就不该按分数评判
        if chosen["how"] == "auto" and best < LOW_SCORE:
            lowsc.append((o["kwh"], k, best, chosen))

        # ④ 区域
        ref, pt = AREA_REF.get(o["area"]), chosen.get("pt")
        if ref and valid(pt):
            d = haversine_km(ref, (float(pt[0]), float(pt[1])))
            if d >= AREA_FAR_KM:
                far.append((o["kwh"], k, d, chosen))

    # ③ 事业者消歧后仍一名多原名
    merged = []
    for k, v in occ.items():
        raws = sorted(v["raw"])
        if len(raws) < 2:
            continue
        core = {"".join(ch for ch in r if not ch.isdigit())
                .replace("号機", "").replace("号系列", "").replace("軸", "")
                .replace(" ", "").replace("　", "") for r in raws}
        if len(core) > 1:
            merged.append((v["kwh"], k, raws, sorted(v["cd"])))

    print()
    print("═" * 72)
    print(f"  已匹配电站                    {len(got):>5}")
    print(f"  ① 消歧没把握（分差<{args.gap:.0f} 且距离≥{args.km:.0f}km） {len(shaky):>5}")
    print(f"  ② 选中分数 <{LOW_SCORE:.0f}（几乎没有正向信号）  {len(lowsc):>5}")
    print(f"  ③ 一名多原名（消歧漏网）        {len(merged):>5}")
    print(f"  ④ 距区域参考点 ≥{AREA_FAR_KM:.0f}km        {len(far):>5}")
    print("═" * 72)

    def head(title, rows, empty):
        if not rows:
            print(f"\n{empty}")
            return False
        print(f"\n{title}")
        return True

    if head(f"① 消歧没把握（前 {args.show}，按発電量降序）：", shaky,
            "① ✅ 无「分差小且位置远」的候选"):
        for kwh, k, gap, d, a, b in sorted(shaky, reverse=True)[:args.show]:
            print(f"  {kwh/tot*100:5.2f}%  {k[1]:14} 分差 {gap:5.1f}  相距 {d:7.1f} km")
            print(f"            选中 {a['source']:4}/{a.get('layer','')[:18]:18} {a['raw'][:30]}")
            print(f"            亚军 {b['source']:4}/{b.get('layer','')[:18]:18} {b['raw'][:30]}")

    if head(f"② 选中分数低（前 {args.show}）：", lowsc,
            "② ✅ 所有选中候选都有正向信号"):
        for kwh, k, sc, c in sorted(lowsc, reverse=True)[:args.show]:
            print(f"  {kwh/tot*100:5.2f}%  {k[1]:14} score={sc:5.1f}  "
                  f"{c['source']}/{c.get('layer','')} {c['raw'][:34]}")

    if head(f"③ 一名多原名 —— 逐条人工核对：", merged,
            "③ ✅ 事业者消歧后无残留错并"):
        for kwh, k, raws, cds in sorted(merged, reverse=True)[:args.show]:
            print(f"  {kwh/tot*100:5.2f}%  「{k[0] or '—'}／{k[1]}」 htdnsCd={cds}")
            for r in raws[:4]:
                print(f"            {r}")

    if head("④ 坐标远离所属区域（⚠️ 电力区域≠地理地方，需人工判）：", far,
            "④ ✅ 无跨岛级可疑坐标"):
        for kwh, k, d, c in sorted(far, reverse=True)[:args.show]:
            print(f"  {kwh/tot*100:5.2f}%  {k[1]:14} {d:7.1f} km  area={occ[k]['area']} "
                  f"@{c['pt']}  {c['raw'][:26]}")

    report.emit(
        matched=len(got), shaky=len(shaky), low_score=len(lowsc),
        merged=len(merged), far_from_area=len(far),
        # ⚠️ 全 0 才是干净；非 0 必须逐条人工确认是误报（LESSONS #23）
        clean=not (shaky or lowsc or merged or far),
        output=str(paths.structured("plants") / "verify.json"),
    )

    if args.dump is not None:
        out = (Path(args.dump) if args.dump
               else paths.structured("plants") / "verify.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "matched": len(got),
            "shaky": [{"name": k[1], "operator": k[0], "gap": round(gp, 1),
                       "km": round(d, 1), "share": kwh / tot}
                      for kwh, k, gp, d, _a, _b in shaky],
            "low_score": [{"name": k[1], "score": round(sc, 1), "share": kwh / tot}
                          for kwh, k, sc, _c in lowsc],
            "merged": [{"name": k[1], "operator": k[0], "raw_names": r,
                        "htdns_cd": cd, "share": kwh / tot}
                       for kwh, k, r, cd in merged],
            "far_from_area": [{"name": k[1], "km": round(d, 1), "share": kwh / tot}
                              for kwh, k, d, _c in far],
        }, ensure_ascii=False), encoding="utf-8")
        log.info("写出校验结果 → %s", out)


if __name__ == "__main__":
    main()
