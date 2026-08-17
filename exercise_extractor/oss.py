"""OSS/MinIO 图片上传：把提取结果里的本地图片引用替换为 OSS URL。

配合本地 MinIO（见仓库根 start_minio.sh）或任意 S3 兼容服务使用。
只要调用方传了 oss_* 参数（endpoint/access_key/secret_key/bucket），
提取完成前会把 markdown 内容里 `<img src="本地路径">` 的图片上传，
并把 src 替换成 OSS 的公开 URL；没传 oss 配置时完全不生效。
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_IMG_SRC_RE = re.compile(r'<img\s+[^>]*?src="([^"]+)"')


@dataclass
class OssConfig:
    """OSS/MinIO 连接配置。"""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: Optional[str] = None
    prefix: str = ""
    use_ssl: bool = False
    public_base_url: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(
            self.endpoint
            and self.access_key
            and self.secret_key
            and self.bucket
        )


def oss_config_from_args(args) -> Optional[OssConfig]:
    """从 CLI/API 参数构造 OSS 配置；四个必填项缺一即返回 None（不启用）。"""
    endpoint = getattr(args, "oss_endpoint", None)
    access_key = getattr(args, "oss_access_key", None)
    secret_key = getattr(args, "oss_secret_key", None)
    bucket = getattr(args, "oss_bucket", None)
    if not (endpoint and access_key and secret_key and bucket):
        return None
    return OssConfig(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
        region=getattr(args, "oss_region", None),
        prefix=getattr(args, "oss_prefix", "") or "",
        use_ssl=bool(getattr(args, "oss_use_ssl", False)),
        public_base_url=getattr(args, "oss_public_base_url", None),
    )


def _minio_client(cfg: OssConfig):
    from minio import Minio

    return Minio(
        cfg.endpoint,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        secure=cfg.use_ssl,
        region=cfg.region,
    )


def _object_key(cfg: OssConfig, local_path: str, markdown_path: str) -> str:
    """对象键：相对 markdown 缓存根目录的路径（如 page_3/imgs/x.jpg），
    加上配置的 prefix，保证不同页面同名图片不互相覆盖。"""
    rel = os.path.relpath(local_path, markdown_path).replace(os.sep, "/")
    if cfg.prefix:
        return f"{cfg.prefix.rstrip('/')}/{rel}"
    return rel


def _public_url(cfg: OssConfig, key: str) -> str:
    """公开 URL：优先 public_base_url，否则按 endpoint/bucket/key 拼。"""
    if cfg.public_base_url:
        return f"{cfg.public_base_url.rstrip('/')}/{key}"
    scheme = "https" if cfg.use_ssl else "http"
    return f"{scheme}://{cfg.endpoint}/{cfg.bucket}/{key}"


def upload_images(
    content: str,
    markdown_path: str,
    cfg: OssConfig,
    cache: Optional[dict] = None,
) -> tuple[str, int]:
    """把 content 里的本地图片上传到 OSS，src 替换为 OSS URL。

    返回 (替换后的 content, 本次实际上传数)。同一本地路径在 cache 里
    命中时直接复用 URL，不重复上传；src 已是 http(s) 的跳过。
    """
    if cache is None:
        cache = {}
    client = None
    uploaded = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal client, uploaded
        src = m.group(1)
        if src.startswith(("http://", "https://")):
            return m.group(0)
        if src in cache:
            return m.group(0).replace(src, cache[src], 1)
        if not os.path.isfile(src):
            logger.warning("OSS 图片不存在，跳过: %s", src)
            return m.group(0)
        key = _object_key(cfg, src, markdown_path)
        if client is None:
            client = _minio_client(cfg)
            try:
                if not client.bucket_exists(cfg.bucket):
                    client.make_bucket(cfg.bucket, location=cfg.region)
            except Exception as e:  # 已存在/权限不足等：继续尝试上传，报错留给上传
                logger.debug("检查/创建桶失败（继续尝试上传）: %s", e)
        content_type = (
            "image/jpeg"
            if src.lower().endswith((".jpg", ".jpeg"))
            else "image/png"
        )
        client.fput_object(
            cfg.bucket, key, src, content_type=content_type
        )
        url = _public_url(cfg, key)
        cache[src] = url
        uploaded += 1
        return m.group(0).replace(src, url, 1)

    new_content = _IMG_SRC_RE.sub(repl, content)
    return new_content, uploaded


def apply_oss_images(
    sections: list,
    markdown_path: str,
    cfg: OssConfig,
    cache: Optional[dict] = None,
) -> int:
    """遍历习题节点树，把各字段里的本地图片上传并替换为 OSS URL。

    覆盖 exercise_content、每道习题的 content/solution_steps/answer，
    以及答案节点的 answer_sections。返回实际上传图片数。
    """
    total = 0

    def walk(nodes: list) -> None:
        nonlocal total
        for node in nodes:
            if node.get("exercise_content"):
                new_text, n = upload_images(
                    node["exercise_content"], markdown_path, cfg, cache
                )
                total += n
                if new_text != node["exercise_content"]:
                    node["exercise_content"] = new_text
            for item in node.get("exercises", []):
                for field in ("content", "solution_steps", "answer"):
                    text = item.get(field)
                    if text:
                        new_text, n = upload_images(
                            text, markdown_path, cfg, cache
                        )
                        total += n
                        if new_text != text:
                            item[field] = new_text
            for sec in node.get("answer_sections", []):
                for item in sec.get("answers", []):
                    for field in ("content", "solution_steps", "answer"):
                        text = item.get(field)
                        if text:
                            new_text, n = upload_images(
                                text, markdown_path, cfg, cache
                            )
                            total += n
                            if new_text != text:
                                item[field] = new_text
            walk(node.get("children", []))

    walk(sections)
    return total
