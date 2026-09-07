"""Validated local image assets; provider URLs are built only from these files."""

import base64
import hashlib
import os
import re
import tempfile
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class ImageStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path(self, image_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", image_id):
            raise ValueError("Invalid image ID")
        return self.directory / image_id

    def describe(self, image_id: str) -> dict:
        path = self.path(image_id)
        with Image.open(path) as image:
            return {
                "id": image_id,
                "url": f"/v1/images/{image_id}",
                "mime_type": IMAGE_TYPES[image.format],
                "width": image.width,
                "height": image.height,
                "size_bytes": path.stat().st_size,
            }

    def save(self, data: bytes, media_type: str) -> dict:
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("图片不能为空且不能超过 5 MiB。")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    if IMAGE_TYPES.get(image.format) != media_type:
                        raise ValueError("图片实际格式与声明不一致；仅支持 JPEG、PNG、WebP。")
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise ValueError("图片不能超过 2000 万像素。")
                    if getattr(image, "is_animated", False):
                        raise ValueError("请上传静态图片，暂不支持动画图片。")
                    image.verify()
                with Image.open(BytesIO(data)) as image:
                    image.load()
        except (
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise ValueError("图片损坏或无法读取。") from exc
        image_id = hashlib.sha256(data).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
            os.replace(temporary, self.path(image_id))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return self.describe(image_id)

    def content(self, text: str, image_ids: list[str]):
        if not image_ids:
            return text
        blocks = [{"type": "text", "text": text or "请描述图片中与训练相关的内容。"}]
        for image_id in image_ids:
            asset = self.describe(image_id)
            data = base64.b64encode(self.path(image_id).read_bytes()).decode("ascii")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{asset['mime_type']};base64,{data}",
                        "detail": "auto",
                    },
                }
            )
        return blocks
