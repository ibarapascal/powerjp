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
import re
import shutil
import zipfile
from pathlib import Path

from pipeline.core import fetch, paths, report

# ⚠️ 版はコードに書かず、配布ページから拾う（CLAUDE.md「上流の版を書かない」）。
#
# 2026-08-02 時点で配布されているのは P03-95 / P03-07 / P03-13 の 3 つだけで、
# zip があるのは **P03-13（平成25年度＝2013年度）のみ**。
# 古い 2 つは GML 形式で製品仕様が別物なので、そもそも読めない。
#
# ★ この源は **12 年間更新されていない**。だから固定しても当面は動くが、
#   固定すると「更新されたのに気づかない」状態が永続する。
#   毎回ページを見て、新しい版が出たら気づけるようにしておく。
LIST_URL = "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P03.html"
FALLBACK_VERSION = "P03-13"     # ページが取れないときの既知の最新

# 読み込みを検証済みの版。これ以外が来たら形式が違う可能性がある
KNOWN_GOOD = {"P03-13"}


def discover_version() -> str:
    """配布ページから zip のある最新の版を拾う。

    ⚠️ 新しい版が出ても形式が同じとは限らない（P03-07 以前は GML）。
    未検証の版を掴んだら**警告を出す** —— 黙って進めて下流が壊れるより、
    人が形式を確認してから進める方がよい。
    """
    try:
        text = fetch.get(LIST_URL).text
    except Exception as exc:                       # noqa: BLE001
        log.warning("配布ページを取得できず（%s）。既知の最新 %s を使う",
                    type(exc).__name__, FALLBACK_VERSION)
        return FALLBACK_VERSION
    vs = sorted(set(re.findall(r"/data/P03/(P03-\d+)/P03-\d+\.zip", text)),
                key=lambda v: int(v.split("-")[1]))
    if not vs:
        log.warning("配布ページに zip リンクが見当たらない。既知の最新 %s を使う",
                    FALLBACK_VERSION)
        return FALLBACK_VERSION
    latest = vs[-1]
    if latest not in KNOWN_GOOD:
        log.warning("★ 未検証の版 %s を検出（既知: %s）。"
                    "GML から shapefile への変更のように形式が変わる場合があるので、"
                    "下流の読み込みを確認すること", latest, ", ".join(sorted(KNOWN_GOOD)))
    elif len(vs) > 1:
        log.info("配布中の版: %s → 最新 %s", ", ".join(vs), latest)
    return latest

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


def count_records(shp_dir: Path, version: str) -> dict[str, int]:
    """数每个 shapefile 的记录数。用 .shx 索引文件算：头 100 字节 + 每条 8 字节。"""
    out: dict[str, int] = {}
    for shx in sorted(shp_dir.glob("*.shx")):
        layer = shx.stem.replace(f"{version}-g_", "")
        out[layer] = max(0, (shx.stat().st_size - 100) // 8)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="国土数値情報 発電施設 P03 ingest")
    ap.add_argument("--version", help="版を固定する（省略時は配布ページの最新）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    fetch.setup_logging()

    version = args.version or discover_version()
    url = f"https://nlftp.mlit.go.jp/ksj/gml/data/P03/{version}/{version}.zip"
    out_dir = paths.raw("ksj_p03", version)
    if args.dry_run:
        log.info("dry-run —— 下载 %s → %s", url, out_dir)
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{version}.zip"
    fetch.download(url, zip_path, force=args.force)
    extracted = extract(zip_path, out_dir / "extracted", force=args.force)

    counts = count_records(extracted / "SHP", version)
    size = paths.dir_size(out_dir)
    log.info("解压完成：%s", extracted)
    for layer, n in sorted(counts.items(), key=lambda x: -x[1]):
        log.info("  %-36s %6d 条", layer, n)
    log.info("合计 %d 条 / %s", sum(counts.values()), report.human_bytes(size))

    report.emit(
        path=str(out_dir),
        bytes=size,
        size_human=report.human_bytes(size),
        version=version,
        # ⚠️ 上流は 2013 年度版から更新されていない。版が変わったら形式も要確認
        version_pinned=bool(args.version),
        rows=sum(counts.values()),
        by_layer=counts,
        has_coordinates=True,           # ★ 这是它相对 JEPOC / FIT 的关键优势
        vintage="2013 年度（平成25年度）" if version == "P03-13" else version,
        crs="见各 .prj（JGD2000 系）",
        licence="国土数値情報 利用約款（政府开放数据，需注明出处）",
        source_url="https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P03.html",
    )


if __name__ == "__main__":
    main()
