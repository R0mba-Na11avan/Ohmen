"""Images attached to chat turns (`.pcb_assistant/chat_images/`).

The composer uploads each image here first and sends only its id over the chat
WebSocket, so frames stay small and the transcript can show the image again when
an old chat is reopened. With no project open they land in a temp dir instead.
"""
from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path

IMAGES_REL = Path(".pcb_assistant") / "chat_images"
MAX_BYTES = 5 * 1024 * 1024  # the providers' per-image ceiling

_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
_MEDIA = {ext: media for media, ext in _EXT.items()}
# Ids are generated here, never taken from a filename — so they are safe path parts.
_ID = re.compile(r"^[0-9a-f]{16}\.(png|jpg|gif|webp)$")


def images_dir(project: str | Path | None) -> Path:
    if project is None:
        return Path(tempfile.gettempdir()) / "pcb_assistant_chat_images"
    return Path(project) / IMAGES_REL


def save_image(project: str | Path | None, data: bytes, media_type: str) -> str:
    """Store one image; return its id. Raises ValueError on a bad type or size."""
    ext = _EXT.get(media_type)
    if ext is None:
        raise ValueError(f"unsupported image type '{media_type}' (want PNG, JPEG, GIF or WebP)")
    if not data:
        raise ValueError("empty image")
    if len(data) > MAX_BYTES:
        raise ValueError(f"image is over {MAX_BYTES // (1024 * 1024)} MB")
    image_id = uuid.uuid4().hex[:16] + ext
    folder = images_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / image_id).write_bytes(data)
    return image_id


def image_path(project: str | Path | None, image_id: str) -> Path | None:
    """The stored file for `image_id`, or None if the id is malformed or unknown."""
    if not _ID.match(image_id or ""):
        return None
    path = images_dir(project) / image_id
    return path if path.is_file() else None


def media_type_of(image_id: str) -> str:
    return _MEDIA[Path(image_id).suffix]


def delete_images(folder: Path, image_ids: list[str]) -> None:
    for image_id in image_ids:
        if _ID.match(image_id or ""):
            (folder / image_id).unlink(missing_ok=True)
