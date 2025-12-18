import time
from typing import Dict, Tuple

import pytesseract
from PIL import Image, ImageFilter, ImageOps


def _preprocess(image: Image.Image) -> Image.Image:
    # Convert to grayscale and improve contrast for printed text on colored backgrounds.
    gray = image.convert("L")
    gray = ImageOps.autocontrast(gray)

    # Light sharpening to crisp characters.
    gray = gray.filter(ImageFilter.SHARPEN)

    # Upscale small images to give tesseract more pixels to work with.
    min_dim = min(gray.size)
    if min_dim < 1000:
        scale = min(2, 1000 / max(min_dim, 1))
        new_size = (int(gray.size[0] * scale), int(gray.size[1] * scale))
        gray = gray.resize(new_size, Image.LANCZOS)

    # Simple binarization to reduce background noise.
    gray = gray.point(lambda p: 255 if p > 180 else 0)
    return gray


def run_tesseract(image: Image.Image) -> Tuple[str, Dict]:
    start = time.time()
    processed = _preprocess(image)
    data = pytesseract.image_to_data(
        processed,
        output_type=pytesseract.Output.DICT,
        config="--psm 6 --oem 3",
    )
    texts = [txt for txt in data.get("text", []) if txt and txt.strip()]
    raw_text = "\n".join(texts)
    confs = [float(c) for c in data.get("conf", []) if c not in ("-1", "", None)]
    mean_conf = sum(confs) / len(confs) if confs else None
    duration_ms = int((time.time() - start) * 1000)
    metrics = {
        "confidence": mean_conf if mean_conf is not None else 0,
        "char_count": len(raw_text),
        "duration_ms": duration_ms,
    }
    return raw_text, metrics

