"""国土数値情報 発電施設（P03）ingest（type: auto）。

国交省的政府开放数据。**唯一一个「按电源种分层 + 带坐标 + 政府开放许可」的发电设施点数据集。**

## 为什么它重要

立项调研把它判为「最后更新 2013 年度，推测已停止维护 → 只能当历史底料」，
所以一直没碰。**这个判断对新建设备成立，对存量设备不成立**：

未匹配的水力（奥只見 1960、田子倉 1959、第二沼沢 1982…）全是**几十年的老电站**，
2013 年的坐标今天照样准。水力/揚水/原子力/大型火力这类长寿命资产，
**2013 年度的坐标是完全可用的**。

只有光伏/风力这种 2013 之后爆发式增长的，覆盖会明显不足 —— 那部分靠 FIT（C1）补。

## 内容

一个 zip（1.4MB）含 9 个 shapefile + 一份 GML：

| shapefile | 内容 |
|---|---|
| `GeneralHydroelectricPowerPlant` | 一般水力 |
| **`PumpedStorageHydroelectricPlant`** | **揚水** |
| `PondInformation` | 揚水的调整池 |
| `ThermalPowerPlant` | 火力 |
| `NuclearPowerPlant` | 原子力 |
| `GeothermalPowerPlant` | 地熱 |
| `WindPowerPlant` | 風力 |
| `PhotovoltaicPowerPlant` | 太陽光（dbf 19MB，条数最多） |
| `BiomassPowerStation` | バイオマス |
| `PowerPlantComplex` | 複合型 |

属性：**位置（点）/ 発電施設名称 / 事業者名 / 所在地 / 号機 / 出力**。

用法：
    python -m pipeline.sources.ksj_p03.ingest
    python -m pipeline.sources.ksj_p03.ingest --force
    python -m pipeline.sources.ksj_p03.ingest --dry-run
"""

from __future__ import annotations

import argparse
import logging
import shutil
import zipfile
from pathlib import Path

from pipeline.core import fetch, paths, report

# 2013 年度版（平成25年度）。更早的 P03-95 / P03-07 是 GML 格式、另一套产品规格。
VERSION = "P03-13"
URL = f"https://nlftp.mlit.go.jp/ksj/gml/data/P03/{VERSION}/{VERSION}.zip"

log = logging.getLogger("powerjp.ksj_p03")


def extract(zip_path: Path, dest: Path, *, force: bool) -> Path:
    """解压。⚠️ zip 内目录名是 Shift_JIS 编码的日文，Python 会按 cp437 解成乱码 ——
    所以按**英文 shapefile 名**重新组织，不保留原目录结构。"""
    if force and dest.exists():
        shutil.rmtree(dest)
    if dest.exists():
        log.info("已解压，跳过")
        return dest
    tmp = dest.with_name(dest.name + ".part")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            # GML 单独放，SHP 平铺（英文名本身就唯一）
            sub = "GML" if name.lower().endswith((".xml",)) else "SHP"
            out = tmp / sub / name
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    tmp.replace(dest)
    return dest


def count_records(shp_dir: Path) -> dict[str, int]:
    """数每个 shapefile 的记录数。用 .shx 索引文件算：头 100 字节 + 每条 8 字节。"""
    out: dict[str, int] = {}
    for shx in sorted(shp_dir.glob("*.shx")):
        layer = shx.stem.replace(f"{VERSION}-g_", "")
        out[layer] = max(0, (shx.stat().st_size - 100) // 8)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="国土数値情報 発電施設 P03 ingest")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    fetch.setup_logging()

    out_dir = paths.raw("ksj_p03", VERSION)
    if args.dry_run:
        log.info("dry-run —— 下载 %s → %s", URL, out_dir)
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{VERSION}.zip"
    fetch.download(URL, zip_path, force=args.force)
    extracted = extract(zip_path, out_dir / "extracted", force=args.force)

    counts = count_records(extracted / "SHP")
    size = paths.dir_size(out_dir)
    log.info("解压完成：%s", extracted)
    for layer, n in sorted(counts.items(), key=lambda x: -x[1]):
        log.info("  %-36s %6d 条", layer, n)
    log.info("合计 %d 条 / %s", sum(counts.values()), report.human_bytes(size))

    report.emit(
        path=str(out_dir),
        bytes=size,
        size_human=report.human_bytes(size),
        version=VERSION,
        rows=sum(counts.values()),
        by_layer=counts,
        has_coordinates=True,           # ★ 这是它相对 JEPOC / FIT 的关键优势
        vintage="2013 年度（平成25年度）",
        crs="见各 .prj（JGD2000 系）",
        licence="国土数値情報 利用約款（政府开放数据，需注明出处）",
        source_url="https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P03.html",
    )


if __name__ == "__main__":
    main()
