"""
app/services/cloudinary_service.py — Image upload via Cloudinary.

Used by:
  - POST /officers/me/photo  → officer profile photo
  - POST /ads/{id}/photo     → agrodealer product photo (admin)

All images are stored under organised folder paths so Cloudinary's
media library stays clean. Public IDs are deterministic so re-uploading
the same officer's photo replaces the old one automatically.

Cloudinary SDK note
───────────────────
The cloudinary SDK is synchronous. We run it in a thread pool executor
to avoid blocking the async event loop.
"""

import asyncio
import logging
from functools import partial

import cloudinary
import cloudinary.uploader

from app.core.config import settings

log = logging.getLogger("agricore.cloudinary")


def _configure() -> None:
    """Configure the Cloudinary SDK from settings (idempotent)."""
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )


_configure()


# ── Upload helpers ────────────────────────────────────────────────────────────

async def upload_officer_photo(file_bytes: bytes, officer_id: int) -> str:
    """Upload an officer's profile photo. Returns the secure URL.

    Public ID is fixed per officer — re-uploading replaces the old image.
    Image is resized to 400x400, cropped to face, converted to webp.
    """
    public_id = f"agricore/officers/{officer_id}/profile"

    def _upload():
        return cloudinary.uploader.upload(
            file_bytes,
            public_id=public_id,
            overwrite=True,
            transformation=[
                {"width": 400, "height": 400, "crop": "fill", "gravity": "face"},
                {"format": "webp", "quality": "auto:good"},
            ],
        )

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _upload)
        url: str = result["secure_url"]
        log.info("Officer photo uploaded: officer_id=%d url=%s", officer_id, url)
        return url
    except Exception as exc:
        log.error("Cloudinary upload failed for officer_id=%d: %s", officer_id, exc)
        raise


async def upload_ad_photo(file_bytes: bytes, ad_id: int) -> str:
    """Upload a product photo for an input ad. Returns the secure URL.

    Resized to 600x400 landscape, suitable for card display.
    """
    public_id = f"agricore/ads/{ad_id}/product"

    def _upload():
        return cloudinary.uploader.upload(
            file_bytes,
            public_id=public_id,
            overwrite=True,
            transformation=[
                {"width": 600, "height": 400, "crop": "fill"},
                {"format": "webp", "quality": "auto:good"},
            ],
        )

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _upload)
        url: str = result["secure_url"]
        log.info("Ad photo uploaded: ad_id=%d url=%s", ad_id, url)
        return url
    except Exception as exc:
        log.error("Cloudinary upload failed for ad_id=%d: %s", ad_id, exc)
        raise


async def delete_image(public_id: str) -> None:
    """Delete an image from Cloudinary by public_id."""
    def _delete():
        return cloudinary.uploader.destroy(public_id)

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _delete)
        log.info("Cloudinary image deleted: %s", public_id)
    except Exception as exc:
        log.warning("Cloudinary delete failed for %s: %s", public_id, exc)
