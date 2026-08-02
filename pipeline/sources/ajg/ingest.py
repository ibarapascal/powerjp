"""All-Japan-Grid 送电网几何 ingest（type: auto）。

取两份东西，缺一不可：

1. **源码 tarball**（tag 归档）—— 主源几何在 git repo 的 `data/` 下，
   30 个「区域 × {lines,substations,plants}」GeoJSON。这是一次源。
2. **release bundle**（full.zip）—— datapackage.json 声明的 9 个配信资源，
   外加 dist/ybus、dist/matpower_national、dist/cim_level2 等建模用产物，
   以及 docs/data/built/（拓扑正典，含逐区域连通性统计）。

⚠️ 两者的 `lines_all` 件数不一致是**上游已知现象**，不是下载出错：
DATA_CATALOG.md（2026-06-03 实测）记 40,077，而 v1.6.0 bundle 的配信层是
去重/合并后的数量。本脚本两边都数、都记进 metadata，不做取舍。

版本策略见 INGEST.md：固定 tag，不追 latest。

用法：
    python -m pipeline.sources.ajg.ingest
    python -m pipeline.sources.ajg.ingest                 # GitHub の最新リリース
    python -m pipeline.sources.ajg.ingest --tag v1.6.0    # 版を固定（再現性が要るとき）
    python -m pipeline.sources.ajg.ingest --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tarfile
import zipfile
from pathlib import Path

from pipeline.core import fetch, paths, report

REPO = "lutelute/All-Japan-Grid"
VARIANT = "full"

REGIONS = [
    "hokkaido", "tohoku", "tokyo", "chubu", "hokuriku",
    "kansai", "chugoku", "shikoku", "kyushu", "okinawa",
]
KINDS = ["lines", "substations", "plants"]

log = logging.getLogger("powerjp.ajg")


def latest_tag() -> str:
    """GitHub Releases API から最新のタグを取る。

    ## ⚠️ 版をコードに書かない

    `DEFAULT_TAG = "v1.6.0"` と固定すると、上流が更新されても永久に
    古い版を取り続ける。しかも成功するので気づけない（LESSONS #34）。

    ## 取得できないときは落とす

    ネットワーク障害や API 制限のときに「とりあえず既知の版で」と
    フォールバックすると、**古い版で成功したように見える**。
    それが一番まずいので、取れなければ例外にして人に判断させる。

    ⚠️ 再現性が要るときは `--tag` で明示的に固定すること。
    """
    r = fetch.get(f"https://api.github.com/repos/{REPO}/releases/latest",
                  headers={"Accept": "application/vnd.github+json"})
    tag = (r.json() or {}).get("tag_name")
    if not tag:
        raise RuntimeError(
            f"{REPO} の最新リリースを判定できない。--tag で明示するか、"
            "GitHub API の状態を確認する")
    return str(tag)


def local_versions() -> list[str]:
    """既に取り込んである版。新旧の比較と重複ダウンロード回避に使う。"""
    base = paths.raw("ajg")
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name.startswith("v"))


def urls(tag: str) -> dict[str, str]:
    base = f"https://github.com/{REPO}"
    stem = f"all-japan-grid-dataset-{tag}-{VARIANT}"
    return {
        "source": f"{base}/archive/refs/tags/{tag}.tar.gz",
        "bundle": f"{base}/releases/download/{tag}/{stem}.zip",
        "manifest": f"{base}/releases/download/{tag}/{stem}.MANIFEST.sha256",
    }


def count_features(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    feats = data.get("features")
    return len(feats) if isinstance(feats, list) else None


def verify_bundle(root: Path, manifest_path: Path) -> dict[str, int]:
    """MANIFEST.sha256 逐文件校验（每行 `<sha256>  <bundle 内相对路径>`）。"""
    ok = bad = missing = 0
    for line in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, rel = parts[0], parts[1].strip()
        target = root / rel
        if not target.is_file():
            missing += 1
            continue
        if fetch.sha256_file(target) == digest:
            ok += 1
        else:
            bad += 1
            log.warning("sha256 不一致：%s", rel)
    log.info("bundle 校验：ok=%d 不一致=%d 缺失=%d", ok, bad, missing)
    if bad:
        raise RuntimeError(f"bundle 有 {bad} 个文件 sha256 不一致，视为下载损坏")
    return {"ok": ok, "mismatch": bad, "missing": missing}


def extract(archive: Path, dest: Path, *, force: bool) -> Path:
    if force and dest.exists():
        shutil.rmtree(dest)
    if dest.exists():
        log.info("已解压，跳过：%s", dest.name)
    else:
        log.info("解压 %s ……", archive.name)
        tmp = dest.with_name(dest.name + ".part")
        if tmp.exists():
            shutil.rmtree(tmp)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmp)
        else:
            with tarfile.open(archive) as tf:
                tf.extractall(tmp, filter="data")
        tmp.replace(dest)
    # 归档顶层通常只有一个目录，返回它
    children = [p for p in dest.iterdir() if p.is_dir()]
    return children[0] if len(children) == 1 else dest


def main() -> None:
    ap = argparse.ArgumentParser(description="All-Japan-Grid 几何 ingest")
    ap.add_argument("--tag", help="版を固定する（省略時は GitHub の最新リリース）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    fetch.setup_logging()

    have = local_versions()
    tag = args.tag or latest_tag()
    if args.tag:
        log.info("版を明示指定: %s", tag)
    elif tag in have:
        log.info("最新リリースは %s。取り込み済み（ローカル: %s）", tag, ", ".join(have))
    else:
        log.info("★ 新しい版を検出: %s（ローカル: %s）", tag, ", ".join(have) or "なし")

    args.tag = tag
    out = paths.raw("ajg", args.tag)
    u = urls(args.tag)

    if args.dry_run:
        log.info("dry-run — 将下载：\n  %s\n  %s\n  %s\n落盘：%s",
                 u["source"], u["bundle"], u["manifest"], out)
        return

    out.mkdir(parents=True, exist_ok=True)
    src_tar = out / f"source-{args.tag}.tar.gz"
    bundle_zip = out / f"bundle-{args.tag}-{VARIANT}.zip"
    manifest = out / f"bundle-{args.tag}-{VARIANT}.MANIFEST.sha256"

    fetch.download(u["source"], src_tar, force=args.force)
    fetch.download(u["manifest"], manifest, force=args.force)
    fetch.download(u["bundle"], bundle_zip, force=args.force)

    src_root = extract(src_tar, out / "source", force=args.force)
    bundle_root = extract(bundle_zip, out / "bundle", force=args.force)
    verify = verify_bundle(bundle_root, manifest)

    # --- 一次源：逐区域件数 ---
    region_counts: dict[str, dict[str, int | None]] = {}
    totals = {k: 0 for k in KINDS}
    for region in REGIONS:
        row: dict[str, int | None] = {}
        for kind in KINDS:
            f = src_root / "data" / f"{region}_{kind}.geojson"
            n = count_features(f) if f.is_file() else None
            row[kind] = n
            if n:
                totals[kind] += n
        region_counts[region] = row

    # supplement 文件是补充几何，单独记，不混入主计数
    supplements = {
        p.name: count_features(p)
        for p in sorted((src_root / "data").glob("*_supplement.geojson"))
    }

    # --- 配信层件数（bundle 的 docs/data） ---
    dist_counts = {
        p.stem: count_features(p)
        for p in sorted((bundle_root / "docs" / "data").glob("*.geojson"))
    }

    # --- 拓扑正典的连通性统计：这是 connectivity_confidence 的依据 ---
    topology: dict[str, dict] = {}
    idx_path = bundle_root / "docs" / "data" / "built" / "index.json"
    if idx_path.is_file():
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        for r in idx.get("regions", []):
            topology[r["id"]] = r.get("stats", {})
        # ⚠️ index.json 的 regions 里含一个 id="all" 的全国缝合结果，
        # 它不是第 11 个区域。求和时必须排除，否则全部翻倍。
        national = topology.get("all", {})
        n_nodes = national.get("n_nodes")
        main_size = national.get("main_size")
        if n_nodes:
            topology["_national"] = {
                "n_nodes": n_nodes,
                "n_edges": national.get("n_edges"),
                "n_components": national.get("n_components"),
                "n_island_nodes": national.get("n_island_nodes"),
                "main_component_ratio": round(main_size / n_nodes, 4) if main_size else None,
            }
            log.info(
                "全国拓扑：节点 %d、边 %d、连通分量 %d、孤岛节点 %d；"
                "主连通分量占 %.1f%% —— 这就是 connectivity_confidence 的现实",
                n_nodes, national.get("n_edges", 0), national.get("n_components", 0),
                national.get("n_island_nodes", 0),
                100 * main_size / n_nodes if main_size else 0,
            )

    size = paths.dir_size(out)
    log.info("一次源合计：lines=%d substations=%d plants=%d",
             totals["lines"], totals["substations"], totals["plants"])
    log.info("完成：%s（%s）", out, report.human_bytes(size))

    report.emit(
        path=str(out),
        tag=args.tag,
        # ⚠️ どの版を使ったかを必ず残す。structured 層は latest_version() で
        #    ローカルの最新を選ぶので、版が変わると下流の数字も変わる
        local_versions=have + ([args.tag] if args.tag not in have else []),
        tag_pinned=bool(args.tag) and args.tag in (have or []),
        bytes=size,
        size_human=report.human_bytes(size),
        source_totals=totals,
        source_by_region=region_counts,
        supplements=supplements,
        distribution_counts=dist_counts,
        topology_stats=topology,
        bundle_verify=verify,
        licence="ODbL (data) / MIT (code)",
        source_url=f"https://github.com/{REPO}/releases/tag/{args.tag}",
    )


if __name__ == "__main__":
    main()
