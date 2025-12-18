# OpenAI-Compatible Chat & Vision API (Jarvis LLM Proxy)

This document summarizes the surface, behavior, and configuration changes made to expose a single OpenAI-style `/v1/chat/completions` endpoint with multimodal (vision) support.

## Public HTTP Surface
- `POST /v1/chat/completions` — the only chat entrypoint (text + vision).
- `GET /health`, `/health/live`, `/health/ready` — health checks.

All legacy routes (`/api/v*/chat`, warmup/model swap/reset/status, etc.) have been removed.

## Request & Response (OpenAI Compatible)

### Request
- `model`: concrete ID or alias (`full`, `lightweight`, `vision`).
- `messages`: list of `{role, content}` where `content` can be:
  - string, or
  - array of parts: `{type:"text", text:"..."}` and/or `{type:"image_url", image_url:{url:"data:<mime>;base64,<...>", detail?: "high"|"low"}}`
- `temperature` (float), `max_tokens` (int, optional), `stream` (bool).

Example (vision):
```json
{
  "model": "vision",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}}
      ]
    }
  ],
  "temperature": 0.2,
  "max_tokens": 200,
  "stream": false
}
```

### Response
Standard OpenAI chat completion shape:
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "<resolved-model-id>",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z}
}
```
Streaming returns `chat.completion.chunk` SSE frames.

### Error Behavior
- If images are sent to a text-only model: `invalid_request_error` explaining the model does not support images.
- Malformed data URLs or decode failures: `invalid_request_error` with decode reason.
- Backend errors surface as `server_error`.

## Model Resolution & Aliases
Handled centrally in `ModelManager`:
- Aliases: `full`, `lightweight`, `vision` → resolve to configured model IDs.
- `get_model_config(model_name)` hides backend details from the route.
- Registry fields include backend type, paths/IDs, context length, `supports_images`.

Env-driven backend selection:
- `JARVIS_MODEL_BACKEND` — primary text model backend.
- `JARVIS_LIGHTWEIGHT_MODEL_BACKEND` — lightweight text backend.
- `JARVIS_VISION_MODEL_BACKEND` — vision backend (supports `MLX-VISION`).

Vision-capable model example: `jarvis-vision-11b` → MLX-Vision (mlx-vlm) Llama 3.2 Vision 4-bit.

## Backends

### Text (unchanged behavior)
- GGUF, MLX, Transformers, VLLM, REST, MOCK — handle text via `generate_text_chat` or legacy `chat/chat_with_temperature`.

### Vision (new)
- `backends/mlx_vision_backend.py` (backend type `MLX-VISION`):
  - Uses `mlx_vlm` (`load`, `load_config`, `apply_chat_template`, `generate`).
  - Builds normalized text prompt with `<image>` placeholders, applies chat template, saves images to temp PNGs, calls `generate(model, processor, prompt, image=..., verbose=False, temperature/max_tokens via kwargs)`.
  - Extracts text from `GenerationResult` (`generations`/`text`).
- Fallback MOCK vision path still available for tests.

## Message Normalization
- Strings are converted to `[TextPart]`.
- Structured arrays allow interleaved text and images; detects presence of any `ImagePart` to route to vision backend.
- Data URLs must be base64; HTTP(S) fetch is out of scope.

## Requirements (Metal / macOS)
- `requirements-metal.txt` now installs both:
  - `mlx-lm` (text, MLX backend)
  - `mlx-vlm` (vision, MLX-VISION backend)
- Base requirements now include `Pillow` for image decoding.

## Configuration Checklist
Set environment variables as needed (examples):
```
JARVIS_MODEL_BACKEND=GGUF          # or MLX/TRANSFORMERS/VLLM/REST/MOCK
JARVIS_LIGHTWEIGHT_MODEL_BACKEND=GGUF
JARVIS_VISION_MODEL_BACKEND=MLX-VISION

JARVIS_MODEL_NAME=jarvis-text-8b
JARVIS_LIGHTWEIGHT_MODEL_NAME=jarvis-text-1b
JARVIS_VISION_MODEL_NAME=.models/mlx-llama-3.2-11b-vision-4bit  # or HF ID, e.g., mlx-community/Llama-3.2-11B-Vision-Instruct-4bit
```

## How to Call (Examples)

### Text-only
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "full",
    "messages": [{"role": "user", "content": "Summarize Jarvis in one sentence."}],
    "temperature": 0.7,
    "max_tokens": 128
  }'
```

### Multimodal (Vision)
```bash
IMG_B64="$(base64 -i /path/to/image.png | tr -d '\n')"
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"vision\",
    \"messages\": [{
      \"role\": \"user\",
      \"content\": [
        {\"type\": \"text\", \"text\": \"Describe this image.\"},
        {\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/png;base64,${IMG_B64}\"}}
      ]
    }],
    \"temperature\": 0.2,
    \"max_tokens\": 200
  }"
```

### Streaming
Set `"stream": true` in either of the above; server replies with SSE `data: {chat.completion.chunk...}` frames.

## Notes & Limits
- Images: only `data:` URLs with base64 are supported. HTTP(S) fetch is not implemented.
- If a model lacks `supports_images`, requests containing images return `invalid_request_error`.
- Ensure `mlx-vlm` is installed and the configured vision model matches the backend (e.g., Llama 3.2 Vision for MLX-VISION).

## Files Touched
- Route: `main.py` — unified `/v1/chat/completions`, normalization, error handling.
- Model management: `managers/model_manager.py` — alias resolution, backend selection, `MLX-VISION` mapping.
- Backends:
  - `backends/mlx_backend.py` — text + basic vision-safe guards.
  - `backends/mlx_vision_backend.py` — mlx-vlm vision implementation.
  - `backends/mock_backend.py` — mock text/vision for tests.
- Requirements: `requirements-base.txt` (Pillow), `requirements-metal.txt` (mlx-lm, mlx-vlm).

