"""把 publish 产物安全地发布到 Cloudflare R2。

发布顺序是：

    1. 从 ``spec.upload_files(scope)`` 得到唯一文件清单
    2. 数据文件写入内容哈希对象（旧对象永不覆盖）
    3. 合并本次 manifest，并把它作为最后一个对象写入
    4. 清理不再被当前 manifest 引用、且已超过 grace window 的旧对象

前端只通过 manifest 发现哈希对象名，因此不会读到「新 manifest + 旧文件」
或「旧 manifest + 被覆盖了一半的文件」这种组合。脚本只使用单线程、64MiB
multipart threshold，避免在私有计算节点上为 20MB 级静态文件开额外并发和内存。

用法：

    uv run python -m pipeline.publish.upload --scope all --dry-run
    uv run python -m pipeline.publish.upload --scope all
    uv run python -m pipeline.publish.upload --scope dynamic

``dynamic`` 第一次发布前必须先成功发布过 ``all``，因为它需要保留 R2 上
现有的 static manifest。非 dry-run 默认会清理旧哈希对象；保留 grace window
是为了让刚好拿着上一版 manifest 的浏览器完成读取。私有计算节点上的 raw 历史不受影响。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import logging
import math
import mimetypes
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from pipeline.core import paths, report
from pipeline.publish import spec

log = logging.getLogger("powerjp.publish.upload")

HASH_CHARS = 16
CHUNK_SIZE = 1024 * 1024
MULTIPART_THRESHOLD = 64 * 1024 * 1024
DEFAULT_PRUNE_GRACE_HOURS = 6.0


class UploadError(RuntimeError):
    """配置、文件或远端 manifest 不满足发布前提。"""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise UploadError(f"缺少环境变量 {name}（请填入 .env，不要写进 git）")
    return value


def _prefix() -> str:
    return os.environ.get("R2_PREFIX", "powerjp/v1").strip(" /")


def _endpoint(account_id: str) -> str:
    return os.environ.get(
        "R2_ENDPOINT", f"https://{account_id}.r2.cloudflarestorage.com"
    ).rstrip("/")


def _publish_dir() -> Path:
    directory = paths.storage() / "publish"
    if not directory.is_dir():
        raise UploadError(f"publish 目录不存在：{directory}")
    return directory


@contextmanager
def _upload_lock():
    """同一台私有计算节点上禁止两个 uploader 同时更新同一个 manifest。"""
    path = paths.storage() / ".r2-upload.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UploadError(
                "已有另一个 R2 上传在运行；为避免 manifest 竞态，本次退出"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _object_name(filename: str, digest: str) -> str:
    path = Path(filename)
    suffix = "".join(path.suffixes)
    stem = filename[: -len(suffix)] if suffix else filename
    return f"assets/{stem}.{digest[:HASH_CHARS]}{suffix}"


def _content_type(filename: str) -> str:
    if filename.endswith(".geojson"):
        return "application/geo+json"
    if filename.endswith(".json"):
        return "application/json"
    if filename.endswith(".txt"):
        return "text/plain"
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _cache_control(filename: str) -> str:
    if filename == "manifest.json":
        return "no-cache,no-store,must-revalidate"
    if filename == "LICENSE.txt":
        return "public,max-age=86400"
    if spec.layer_of(filename) == "static":
        return "public,max-age=31536000,immutable"
    return "public,max-age=60"


def _load_local_manifest(directory: Path) -> dict[str, Any]:
    path = directory / "manifest.json"
    if not path.is_file():
        raise UploadError(f"缺少 {path}；先运行 publish pipeline")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UploadError(f"manifest.json 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise UploadError("manifest.json 顶层必须是 object")
    return data


def _entries(manifest: dict[str, Any], layer: str) -> dict[str, dict[str, Any]]:
    raw = manifest.get(layer, [])
    if not isinstance(raw, list):
        raise UploadError(f"manifest.{layer} 必须是 array")
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise UploadError(f"manifest.{layer} 有无效条目：{item!r}")
        result[item["file"]] = dict(item)
    return result


def _local_entries(manifest: dict[str, Any], filename: str) -> dict[str, Any]:
    for layer in spec.LAYER_FILES:
        for item in _entries(manifest, layer).values():
            if item["file"] == filename:
                return item
    if filename == "LICENSE.txt":
        return {"file": filename}
    raise UploadError(f"manifest 没有记录 {filename}")


def _load_remote_manifest(client: Any, bucket: str, key: str) -> dict[str, Any] | None:
    from botocore.exceptions import ClientError

    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NoSuchBucket"}:
            return None
        raise
    body = response["Body"].read()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UploadError(f"R2 上的 manifest.json 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise UploadError("R2 上的 manifest.json 顶层必须是 object")
    return data


def _remote_key(name: str) -> str:
    prefix = _prefix()
    return f"{prefix}/{name}" if prefix else name


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _prune_grace_hours() -> float:
    raw = os.environ.get(
        "R2_PRUNE_GRACE_HOURS", str(DEFAULT_PRUNE_GRACE_HOURS)
    ).strip()
    try:
        hours = float(raw)
    except ValueError as exc:
        raise UploadError("R2_PRUNE_GRACE_HOURS 必须是非负数字") from exc
    if not math.isfinite(hours) or hours < 0:
        raise UploadError("R2_PRUNE_GRACE_HOURS 必须是非负数字")
    return hours


def _manifest_object_keys(manifest: dict[str, Any]) -> set[str]:
    """返回当前 manifest 引用的全部 R2 key；不完整时拒绝清理。"""
    keys: set[str] = set()
    missing: list[str] = []
    for layer in spec.LAYER_FILES:
        for item in _entries(manifest, layer).values():
            object_name = item.get("object")
            if not isinstance(object_name, str) or not object_name:
                missing.append(item["file"])
            else:
                keys.add(_remote_key(object_name))

    license_name = manifest.get("license")
    if not isinstance(license_name, str) or not license_name:
        missing.append("LICENSE.txt")
    else:
        keys.add(_remote_key(license_name))

    if missing:
        raise UploadError(
            "当前 manifest 不是完整的哈希 manifest，拒绝自动清理；缺少 object："
            + ", ".join(missing)
        )
    return keys


def _prune_orphans(
    client: Any,
    bucket: str,
    manifest: dict[str, Any],
    *,
    grace_hours: float,
) -> tuple[int, int]:
    """删除当前 manifest 不引用的旧 hash 对象，返回 (对象数, 字节数)。

    只扫描 ``R2_PREFIX/assets/``，并且只删除超过 grace window 的对象。
    manifest 已在调用前写成功；因此删除失败不会让前端失去新版本。
    """
    referenced = _manifest_object_keys(manifest)
    prefix = _remote_key("assets/")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=grace_hours)
    candidates: list[tuple[str, int]] = []

    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item.get("Key")
            if not isinstance(key, str) or key in referenced:
                continue
            last_modified = item.get("LastModified")
            if not isinstance(last_modified, datetime):
                # 没有可靠年龄时宁可保留，下一轮再处理。
                continue
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)
            if last_modified > cutoff:
                continue
            candidates.append((key, int(item.get("Size", 0) or 0)))

    deleted_count = 0
    deleted_bytes = 0
    for start in range(0, len(candidates), 1000):
        batch = candidates[start:start + 1000]
        response = client.delete_objects(
            Bucket=bucket,
            Delete={
                "Objects": [{"Key": key} for key, _ in batch],
                "Quiet": True,
            },
        )
        errors = response.get("Errors", [])
        if errors:
            details = "; ".join(
                f"{e.get('Key', '?')}: {e.get('Code', 'unknown')}"
                for e in errors[:5]
            )
            raise UploadError(f"R2 旧对象清理失败：{details}")
        deleted_count += len(batch)
        deleted_bytes += sum(size for _, size in batch)

    if candidates:
        log.info(
            "prune 旧 hash 对象：%d 个，释放约 %.2f MB（grace %.1f 小时）",
            deleted_count, deleted_bytes / 1_000_000, grace_hours,
        )
    else:
        log.info("prune 无可删除旧 hash 对象（grace %.1f 小时）", grace_hours)
    return deleted_count, deleted_bytes


def _head_matches(
    client: Any,
    bucket: str,
    key: str,
    digest: str,
    size: int,
    filename: str,
) -> bool:
    from botocore.exceptions import ClientError

    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    metadata = {str(k).lower(): str(v) for k, v in response.get("Metadata", {}).items()}
    return (
        response.get("ContentLength") == size
        and metadata.get("sha256") == digest
        and response.get("ContentType") == _content_type(filename)
        and response.get("CacheControl") == _cache_control(filename)
    )


def _upload_file(client: Any, bucket: str, source: Path, key: str, digest: str) -> bool:
    from boto3.s3.transfer import TransferConfig

    if _head_matches(
        client, bucket, key, digest, source.stat().st_size, source.name
    ):
        log.info("skip  %s（远端已有相同 hash）", key)
        return False

    config = TransferConfig(
        multipart_threshold=MULTIPART_THRESHOLD,
        max_concurrency=1,
        use_threads=False,
    )
    client.upload_file(
        str(source),
        bucket,
        key,
        ExtraArgs={
            "ContentType": _content_type(source.name),
            "CacheControl": _cache_control(source.name),
            "Metadata": {"sha256": digest},
        },
        Config=config,
    )
    log.info("upload %s -> %s (%d bytes)", source.name, key, source.stat().st_size)
    return True


def _manifest_entry(
    local: dict[str, Any], filename: str, object_name: str, digest: str, size: int
) -> dict[str, Any]:
    entry = dict(local)
    entry.update({"file": filename, "object": object_name, "sha256": digest, "bytes": size})
    return entry


def _build_remote_manifest(
    local: dict[str, Any], remote: dict[str, Any] | None,
    updated: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if remote is None:
        result: dict[str, Any] = {"built": list(local.get("built", []))}
        for layer in spec.LAYER_FILES:
            result[layer] = list(_entries(local, layer).values())
    else:
        result = dict(remote)

    for layer in spec.LAYER_FILES:
        current = _entries(result, layer)
        for filename, entry in updated.items():
            if filename in spec.LAYER_FILES[layer]:
                current[filename] = entry
        result[layer] = list(current.values())

    # 许可证不是数据层，但要和本次发布一起可发现。
    if "license" in updated:
        result["license"] = updated["license"]["object"]
    result["manifest_version"] = 2
    return result


def _iter_selected(scope: str) -> list[str]:
    selected = spec.upload_files(scope)
    if selected[-1] != "manifest.json":
        raise UploadError("spec.upload_files() 必须把 manifest.json 放在最后")
    return selected


def upload(
    scope: str,
    *,
    dry_run: bool = False,
    prune: bool | None = None,
    prune_grace_hours: float | None = None,
) -> int:
    with _upload_lock():
        return _upload_locked(
            scope,
            dry_run=dry_run,
            prune=prune,
            prune_grace_hours=prune_grace_hours,
        )


def _upload_locked(
    scope: str,
    *,
    dry_run: bool = False,
    prune: bool | None = None,
    prune_grace_hours: float | None = None,
) -> int:
    load_dotenv()
    if prune is None:
        prune = _env_bool("R2_PRUNE_ORPHANS", True)
    if prune_grace_hours is None:
        prune_grace_hours = _prune_grace_hours()
    if not math.isfinite(prune_grace_hours) or prune_grace_hours < 0:
        raise UploadError("--prune-grace-hours 必须是非负数字")
    directory = _publish_dir()
    local = _load_local_manifest(directory)
    selected = _iter_selected(scope)

    records: dict[str, tuple[Path, str, str, int]] = {}
    for filename in selected:
        source = directory / filename
        if not source.is_file():
            raise UploadError(f"发布清单中的文件不存在：{source}")
        digest = _sha256(source)
        object_name = _object_name(filename, digest)
        records[filename] = (source, digest, object_name, source.stat().st_size)

    if dry_run:
        for filename in selected[:-1]:
            source, digest, object_name, size = records[filename]
            layer = spec.layer_of(filename) or "always"
            log.info(
                "dry-run %s %-7s %s -> %s (%d bytes)",
                layer, filename, digest[:HASH_CHARS], object_name, size,
            )
        log.info("dry-run manifest.json（最后写入；scope=%s）", scope)
        return 0

    import boto3

    account_id = _required_env("R2_ACCOUNT_ID")
    access_key = _required_env("R2_ACCESS_KEY_ID")
    secret_key = _required_env("R2_SECRET_ACCESS_KEY")
    bucket = _required_env("R2_BUCKET")
    client = boto3.client(
        "s3",
        endpoint_url=_endpoint(account_id),
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )

    manifest_key = _remote_key("manifest.json")
    remote = _load_remote_manifest(client, bucket, manifest_key)
    if scope != "all" and remote is None:
        raise UploadError("R2 上还没有 manifest.json；首次发布必须使用 --scope all")
    if scope != "all" and remote is not None:
        # 不能拿新 dynamic 去拼旧的 plain-name static（或反过来）。
        # 迁移到哈希对象时先跑一次 all，之后才允许分层增量。
        missing_hash = [
            item["file"]
            for layer in spec.LAYER_FILES
            if layer != scope
            for item in _entries(remote, layer).values()
            if not item.get("object")
        ]
        if missing_hash:
            raise UploadError(
                "R2 manifest 仍有非哈希对象，不能做分层增量；"
                "请先运行 --scope all："
                + ", ".join(missing_hash)
            )

    updated: dict[str, dict[str, Any]] = {}
    uploaded_bytes = 0
    for filename in selected[:-1]:
        source, digest, object_name, size = records[filename]
        if _upload_file(client, bucket, source, _remote_key(object_name), digest):
            uploaded_bytes += size
        local_entry = _local_entries(local, filename)
        entry = _manifest_entry(local_entry, filename, object_name, digest, size)
        if filename == "LICENSE.txt":
            updated["license"] = entry
        else:
            updated[filename] = entry

    remote_manifest = _build_remote_manifest(local, remote, updated)
    manifest_bytes = json.dumps(remote_manifest, ensure_ascii=False, separators=(",", ":")).encode()
    client.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=manifest_bytes,
        ContentType="application/json",
        CacheControl="no-cache,no-store,must-revalidate",
    )
    log.info("manifest.json 最后更新：%s（%d bytes）", manifest_key, len(manifest_bytes))
    log.info(
        "本次实际上传数据：%.2f MB（scope=%s；相同 hash 已跳过）",
        uploaded_bytes / 1_000_000, scope,
    )
    pruned_objects = 0
    pruned_bytes = 0
    if prune:
        pruned_objects, pruned_bytes = _prune_orphans(
            client,
            bucket,
            remote_manifest,
            grace_hours=prune_grace_hours,
        )
    else:
        log.info("跳过旧 hash 对象清理（--no-prune / R2_PRUNE_ORPHANS=0）")
    report.emit(
        status="published",
        scope=scope,
        manifest_version=remote_manifest.get("manifest_version", 2),
        files=len(selected) - 1,
        uploaded_bytes=uploaded_bytes,
        uploaded_size_human=report.human_bytes(uploaded_bytes),
        pruned_objects=pruned_objects,
        pruned_bytes=pruned_bytes,
        path=f"r2://{bucket}/{manifest_key}",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=spec.SCOPES, default="all")
    parser.add_argument(
        "--dry-run", action="store_true", help="只计算 hash 和打印清单，不连接 R2"
    )
    prune = parser.add_mutually_exclusive_group()
    prune.add_argument(
        "--prune", dest="prune", action="store_true",
        help="manifest 更新后清理过期的旧 hash 对象（默认开启）",
    )
    prune.add_argument(
        "--no-prune", dest="prune", action="store_false",
        help="保留旧 hash 对象；仅用于排障或迁移",
    )
    parser.set_defaults(prune=None)
    parser.add_argument(
        "--prune-grace-hours", type=float, default=None,
        help=f"旧对象至少保留多少小时（默认 {DEFAULT_PRUNE_GRACE_HOURS:g}，也可用 .env 配置）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return upload(
            args.scope,
            dry_run=args.dry_run,
            prune=args.prune,
            prune_grace_hours=args.prune_grace_hours,
        )
    except UploadError as exc:
        log.error("发布前检查失败：%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
