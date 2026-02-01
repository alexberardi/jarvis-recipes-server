"""
Client for the Jarvis OCR Service microservice.

This module handles communication with the jarvis-ocr-service API,
which provides OCR capabilities via multiple providers including traditional OCR
(Tesseract, EasyOCR, PaddleOCR, Apple Vision) and LLM-based providers
(llm_proxy_vision, llm_proxy_cloud).
"""
import base64
import logging
from typing import Dict, List, Optional, Tuple

import httpx

from jarvis_recipes.app.core import service_config
from jarvis_recipes.app.core.config import get_settings

logger = logging.getLogger(__name__)


def _get_auth_headers() -> Dict[str, str]:
    """Get authentication headers for OCR service (same as LLM proxy)."""
    settings = get_settings()
    if not settings.jarvis_auth_app_id or not settings.jarvis_auth_app_key:
        raise ValueError("JARVIS_AUTH_APP_ID and JARVIS_AUTH_APP_KEY must be set for OCR service authentication")
    return {
        "X-Jarvis-App-Id": settings.jarvis_auth_app_id,
        "X-Jarvis-App-Key": settings.jarvis_auth_app_key,
    }


async def call_ocr_service_batch(
    image_bytes_list: List[bytes],
    provider: str = "auto",
    content_type: str = "image/jpeg",
    language_hints: Optional[List[str]] = None,
    timeout_seconds: int = 300,  # Higher timeout for batch (especially with LLM providers)
) -> Tuple[str, Optional[float], Optional[str], List[Dict]]:
    """
    Call OCR service batch endpoint for multiple images.
    
    Uses the `/v1/ocr/batch` endpoint which processes all images in a single request.
    The OCR service handles provider selection (including vision/cloud) automatically.

    Args:
        image_bytes_list: List of raw image bytes (1-100 images)
        provider: OCR provider to use ("auto", "tesseract", "easyocr", "paddleocr", 
                 "apple_vision", "llm_proxy_vision", "llm_proxy_cloud")
        content_type: MIME type of the images (assumed same for all)
        language_hints: Optional list of language codes (e.g., ["en", "fr"])
        timeout_seconds: Request timeout in seconds (default: 300 for batch with LLM providers)

    Returns:
        Tuple of (combined_text, mean_confidence, provider_used, metadata_list)
        Raises OCRServiceUnavailableError if service is unavailable
    """
    ocr_url = service_config.get_ocr_url()
    if not ocr_url:
        raise ValueError("JARVIS_OCR_SERVICE_URL not configured")

    if not image_bytes_list:
        raise ValueError("image_bytes_list cannot be empty")

    if len(image_bytes_list) > 100:
        raise ValueError("Maximum 100 images per batch request")

    # Encode all images to base64
    images_payload = []
    for img_bytes in image_bytes_list:
        images_payload.append({
            "content_type": content_type,
            "base64": base64.b64encode(img_bytes).decode("utf-8"),
        })

    payload = {
        "provider": provider,
        "images": images_payload,
        "options": {
            "language_hints": language_hints or ["en"],
            "return_boxes": False,  # We don't need boxes for recipe extraction
            "mode": "document",
        },
    }

    url = f"{ocr_url.rstrip('/')}/v1/ocr/batch"
    headers = {
        "Content-Type": "application/json",
        **_get_auth_headers(),
    }

    try:
        timeout = httpx.Timeout(timeout_seconds, read=timeout_seconds, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code >= 400:
            logger.warning(
                "OCR service batch returned error: status=%s, body=%s",
                resp.status_code,
                resp.text[:1000],
            )
            raise ValueError(f"OCR service error: {resp.status_code} - {resp.text[:500]}")

        data = resp.json()
        results = data.get("results", [])
        batch_meta = data.get("meta", {})
        provider_used = batch_meta.get("provider_used") or results[0].get("provider_used") if results else None

        # Combine text from all results
        texts = []
        confidences = []
        metadata_list = []

        for result in results:
            text = result.get("text", "")
            if text:
                texts.append(text)
            
            # Calculate mean confidence from blocks for this image
            blocks = result.get("blocks", [])
            if blocks:
                img_confidences = [block.get("confidence", 0.0) for block in blocks if "confidence" in block]
                if img_confidences:
                    # Convert from 0-1 to 0-100 scale if needed
                    mean_img_conf = sum(img_confidences) / len(img_confidences)
                    if mean_img_conf <= 1.0:
                        mean_img_conf = mean_img_conf * 100
                    confidences.append(mean_img_conf)
            
            # Store metadata for this image
            img_meta = result.get("meta", {})
            metadata_list.append(img_meta)

        combined_text = "\n\n".join(texts)
        mean_confidence = sum(confidences) / len(confidences) if confidences else None

        logger.info(
            "OCR service batch success: provider=%s, images=%d, text_len=%d, confidence=%s, duration_ms=%s",
            provider_used,
            len(image_bytes_list),
            len(combined_text),
            mean_confidence,
            batch_meta.get("total_duration_ms"),
        )

        return combined_text, mean_confidence, provider_used, metadata_list

    except httpx.TimeoutException:
        logger.warning("OCR service batch request timed out after %ds", timeout_seconds)
        raise ValueError(f"OCR service batch timed out after {timeout_seconds}s")
    except ValueError:
        # Re-raise ValueError (service errors)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("OCR service batch request failed: %s", exc)
        raise ValueError(f"OCR service batch failed: {exc}") from exc

