import time
from typing import Dict, Tuple

import os
import numpy as np
import easyocr
from PIL import Image

from jarvis_recipes.app.core.config import get_settings

_reader_cache = {}


def get_reader():
    settings = get_settings()
    gpu_flag = bool(settings.recipe_ocr_easyocr_gpu)
    model_path = os.getenv("EASY_OCR_MODEL_PATH")
    cache_key = (gpu_flag, model_path)
    if cache_key not in _reader_cache:
        kwargs = {"gpu": gpu_flag}
        if model_path:
            kwargs["model_storage_directory"] = model_path
            # Allow download only if the detector file is missing in the cache.
            detector_file = os.path.join(model_path, "craft_mlt_25k.pth")
            kwargs["download_enabled"] = not os.path.exists(detector_file)
        else:
            # Fall back to baked-in cache location if provided
            baked_path = "/opt/easyocr_cache"
            if os.path.exists(baked_path):
                kwargs["model_storage_directory"] = baked_path
                kwargs["download_enabled"] = False
        _reader_cache[cache_key] = easyocr.Reader(["en"], **kwargs)
    return _reader_cache[cache_key]


def run_easyocr(image: Image.Image) -> Tuple[str, Dict]:
    start = time.time()
    reader = get_reader()
    # EasyOCR expects a numpy array; ensure RGB and convert
    img_np = np.array(image.convert("RGB"))
    results = reader.readtext(img_np)
    parts = []
    confs = []
    for _, text, conf in results:
        parts.append(text)
        try:
            confs.append(float(conf))
        except Exception:
            pass
    raw_text = "\n".join(parts)
    mean_conf = sum(confs) / len(confs) if confs else None
    duration_ms = int((time.time() - start) * 1000)
    metrics = {
        "confidence": mean_conf if mean_conf is not None else 0,
        "char_count": len(raw_text),
        "duration_ms": duration_ms,
    }
    return raw_text, metrics

