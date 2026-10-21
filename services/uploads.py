"""
Listing image uploads: validation, cover-crop to a fixed size, and public URLs.
Files live in app.config["UPLOAD_FOLDER"] and are served from /uploads/<name>.
"""
import uuid
from pathlib import Path

from flask import current_app, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
PILLOW_FORMATS = ("PNG", "JPEG", "WEBP")
LISTING_IMAGE_SIZE = (1100, 750)


class InvalidImageError(ValueError):
    pass


def allowed_image_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_IMAGE_EXTENSIONS


def upload_folder() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"])


def process_listing_image(file_storage):
    """
    Cover-crop an uploaded image to LISTING_IMAGE_SIZE and save it as JPEG under
    a random name. Returns "uploads/<name>.jpg", or None when no file was sent.
    Raises InvalidImageError for disallowed or unreadable files.
    """
    if not file_storage or file_storage.filename == "":
        return None
    if not allowed_image_file(file_storage.filename):
        raise InvalidImageError(file_storage.filename)
    try:
        # Only the decoders we accept: a file named .png that is really, say,
        # a PSD or FITS image never reaches those (historically buggy) decoders.
        with Image.open(file_storage.stream, formats=PILLOW_FORMATS) as src:
            img = ImageOps.exif_transpose(src).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImageError(file_storage.filename) from exc
    img = ImageOps.fit(img, LISTING_IMAGE_SIZE, Image.Resampling.LANCZOS)
    filename = f"{uuid.uuid4().hex}.jpg"
    img.save(upload_folder() / filename, "JPEG", quality=90)
    return f"uploads/{filename}"


def product_image_url(product):
    """Public URL of a listing image, or None when the listing has no image."""
    path = (product.get("image_path") or product.get("image") or "").strip()
    if not path:
        return None
    if path.startswith(("/static/", "http://", "https://")):
        return path
    if path.startswith("uploads/"):
        return url_for("shop.uploaded_file", filename=path[len("uploads/"):])
    return url_for("static", filename=path)
