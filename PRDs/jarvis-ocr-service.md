# Jarvis OCR Service — API Specification

This document describes the REST API for the **Jarvis OCR Service**. It is intended for client developers who need to integrate with the OCR service.

---

## Quick Start

### 1. Get App Credentials

Obtain your app credentials from **Jarvis Auth**:
- `X-Jarvis-App-Id`: Your app identifier (e.g., `jarvis-recipes-server`)
- `X-Jarvis-App-Key`: Your app secret/API key

Contact your Jarvis Auth administrator to obtain these credentials.

### 2. Test Connection

Check if the service is available:

```bash
curl http://localhost:5009/health
```

Expected response:
```json
{
  "status": "ok"
}
```

### 3. Check Available Providers

Discover which OCR providers are available:

```bash
curl -H "X-Jarvis-App-Id: your-app-id" \
     -H "X-Jarvis-App-Key: your-app-key" \
     http://localhost:5009/v1/providers
```

### 4. Perform OCR

Extract text from an image:

```bash
# Encode your image to base64
IMAGE_BASE64=$(base64 -i your-image.png)

curl -X POST http://localhost:5009/v1/ocr \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-App-Id: your-app-id" \
  -H "X-Jarvis-App-Key: your-app-key" \
  -d "{
    \"image\": {
      \"content_type\": \"image/png\",
      \"base64\": \"$IMAGE_BASE64\"
    }
  }"
```

### 5. Integration Checklist

- [ ] Obtain app credentials from Jarvis Auth
- [ ] Verify service is accessible (health check)
- [ ] Check available providers
- [ ] Implement authentication headers on all requests
- [ ] Handle error responses appropriately
- [ ] Implement retry logic for transient errors
- [ ] Use request compression for large images
- [ ] Monitor queue status if using async processing

---

## Base URL and Versioning

- **Base URL**: Configurable (default: `http://localhost:5009`)
- **API Version**: `v1`
- **Content-Type**: `application/json` (for all requests and responses)

All API endpoints are prefixed with `/v1/` except for the health check endpoint.

---

## Authentication

All protected endpoints require **Jarvis Auth app-to-app authentication**. Clients must include the following headers on every protected request:

| Header | Required | Description |
|--------|----------|-------------|
| `X-Jarvis-App-Id` | ✅ | App identifier issued by Jarvis Auth |
| `X-Jarvis-App-Key` | ✅ | App secret (API key) issued by Jarvis Auth |

### Authentication Errors

- **401 Unauthorized**: Missing or invalid app credentials
- **503 Service Unavailable**: Auth service is unreachable

See `prds/auth.md` for detailed authentication documentation.

---

## Common Headers

### Request Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type` | ✅ | Must be `application/json` |
| `X-Jarvis-App-Id` | ✅* | App identifier (required for protected endpoints) |
| `X-Jarvis-App-Key` | ✅* | App secret (required for protected endpoints) |
| `X-Correlation-ID` | ❌ | Optional correlation ID for request tracking |

*Required for protected endpoints only

### Response Headers

All responses include standard HTTP headers. No custom response headers are currently defined.

---

## Error Responses

All error responses follow a consistent format:

```json
{
  "detail": "Error message description"
}
```

### HTTP Status Codes

| Code | Meaning | Description |
|------|---------|-------------|
| `200` | OK | Request succeeded |
| `400` | Bad Request | Invalid request parameters or provider unavailable |
| `401` | Unauthorized | Missing or invalid authentication credentials |
| `422` | Unprocessable Entity | OCR processing failed (invalid image, etc.) |
| `500` | Internal Server Error | Unexpected server error |
| `503` | Service Unavailable | Service not initialized or auth service unavailable |

### Error Response Examples

**400 Bad Request** (Provider unavailable):
```json
{
  "detail": "Provider 'easyocr' is not enabled or available. Available providers: tesseract"
}
```

**401 Unauthorized**:
```json
{
  "detail": {
    "error_code": "unauthorized",
    "error_message": "Missing or invalid app credentials"
  }
}
```

**422 Unprocessable Entity** (OCR processing failure):
```json
{
  "detail": "Failed to process image: Invalid image format"
}
```

**500 Internal Server Error**:
```json
{
  "detail": "Internal server error: <error message>"
}
```

**503 Service Unavailable**:
```json
{
  "detail": "Service not initialized"
}
```

---

## Endpoints Summary

| Method | Endpoint | Auth Required | Description |
|--------|----------|---------------|-------------|
| `GET` | `/health` | ❌ | Health check |
| `GET` | `/v1/providers` | ✅ | Get available OCR providers |
| `GET` | `/v1/queue/status` | ✅ | Get Redis queue status |
| `POST` | `/v1/ocr` | ✅ | Perform OCR on single image |
| `POST` | `/v1/ocr/batch` | ✅ | Perform OCR on multiple images |

---

## Endpoints

### `GET /health`

Health check endpoint. This endpoint is **public** (no authentication required).

#### Request

**Method**: `GET`  
**Path**: `/health`  
**Headers**: None required

#### Response

**Status**: `200 OK`

```json
{
  "status": "ok"
}
```

#### Example

```bash
curl http://localhost:5009/health
```

---

### `GET /v1/queue/status`

Get Redis queue status and statistics. This endpoint is useful for monitoring and health checks by external systems.

#### Request

**Method**: `GET`  
**Path**: `/v1/queue/status`  
**Headers**: 
- `X-Jarvis-App-Id` (required)
- `X-Jarvis-App-Key` (required)

#### Response

**Status**: `200 OK`

```json
{
  "redis_connected": true,
  "queue_length": 0,
  "workers_active": 0,
  "queue_name": "ocr_jobs",
  "redis_info": {
    "host": "redis",
    "port": 6379,
    "version": "7.0.0"
  }
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `redis_connected` | `boolean` | Whether Redis connection is active |
| `queue_length` | `number` | Number of pending jobs in queue |
| `workers_active` | `number` | Number of active workers (if tracking enabled) |
| `queue_name` | `string` | Name of the Redis queue/list |
| `redis_info` | `object` | Redis connection information |
| `redis_info.host` | `string` | Redis hostname |
| `redis_info.port` | `number` | Redis port |
| `redis_info.version` | `string` | Redis server version |

**Error Responses**:

**503 Service Unavailable**: Redis is not available or not configured
```json
{
  "detail": "Redis queue is not available"
}
```

#### Example

```bash
curl -H "X-Jarvis-App-Id: jarvis-recipes-server" \
     -H "X-Jarvis-App-Key: $JARVIS_APP_KEY" \
     http://localhost:5009/v1/queue/status
```

---

### `GET /v1/providers`

Get the list of available OCR providers and their availability status.

#### Request

**Method**: `GET`  
**Path**: `/v1/providers`  
**Headers**: 
- `X-Jarvis-App-Id` (required)
- `X-Jarvis-App-Key` (required)

#### Response

**Status**: `200 OK`

```json
{
  "providers": {
    "tesseract": true,
    "easyocr": false,
    "paddleocr": false,
    "apple_vision": true
  }
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `providers` | `object` | Map of provider names to availability (boolean) |
| `providers.tesseract` | `boolean` | Always `true` (mandatory provider) |
| `providers.easyocr` | `boolean` | `true` if EasyOCR is enabled and available |
| `providers.paddleocr` | `boolean` | `true` if PaddleOCR is enabled and available |
| `providers.apple_vision` | `boolean` | `true` if Apple Vision is enabled and available (macOS only) |
| `providers.llm_proxy_vision` | `boolean` | `true` if LLM Proxy Vision is enabled and available |
| `providers.llm_proxy_cloud` | `boolean` | `true` if LLM Proxy Cloud is enabled and available |

#### Example

```bash
curl -H "X-Jarvis-App-Id: jarvis-recipes-server" \
     -H "X-Jarvis-App-Key: $JARVIS_APP_KEY" \
     http://localhost:5009/v1/providers
```

---

### `POST /v1/ocr`

Perform OCR on a single image. This is the main endpoint for extracting text from images.

**Note**: For processing multiple images, use the batch endpoint `POST /v1/ocr/batch`.

#### Request

**Method**: `POST`  
**Path**: `/v1/ocr`  
**Headers**: 
- `Content-Type: application/json` (required)
- `X-Jarvis-App-Id` (required)
- `X-Jarvis-App-Key` (required)
- `X-Correlation-ID` (optional, for request tracking)

**Request Body**:

```json
{
  "document_id": "optional-string",
  "provider": "auto",
  "image": {
    "content_type": "image/png",
    "base64": "iVBORw0KGgoAAAANSUhEUgAA..."
  },
  "options": {
    "language_hints": ["en"],
    "return_boxes": true,
    "mode": "document"
  }
}
```

**Request Model**:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `document_id` | `string` | ❌ | `null` | Optional document identifier for tracking |
| `provider` | `string` | ❌ | `"auto"` | OCR provider to use. One of: `"auto"`, `"tesseract"`, `"easyocr"`, `"paddleocr"`, `"apple_vision"`, `"llm_proxy_vision"`, `"llm_proxy_cloud"` |
| `image` | `object` | ✅ | - | Image to process |
| `image.content_type` | `string` | ✅ | - | MIME type of the image (e.g., `"image/png"`, `"image/jpeg"`, `"image/jpg"`) |
| `image.base64` | `string` | ✅ | - | Base64-encoded image data (without data URI prefix) |
| `options` | `object` | ❌ | See below | OCR processing options |
| `options.language_hints` | `string[]` | ❌ | `null` | Array of language codes (e.g., `["en", "fr"]`) |
| `options.return_boxes` | `boolean` | ❌ | `true` | Whether to return bounding boxes for each text block |
| `options.mode` | `string` | ❌ | `"document"` | OCR mode. One of: `"document"`, `"single_line"`, `"word"` |

**Provider Selection**:

- `"auto"`: Automatically selects the best available provider in this order (by processing cost/power, cheapest first):
  1. Tesseract (always available)
  2. EasyOCR (if enabled)
  3. PaddleOCR (if enabled)
  4. Apple Vision (if enabled and available)
  5. LLM Proxy Vision (if enabled and available)
  6. LLM Proxy Cloud (if enabled and available)
  
  **Validation Guardrails**: When using `"auto"`, the service validates OCR output quality using the LLM proxy "full" model. If a provider produces garbled/nonsense text, it automatically tries the next provider in the list. This ensures the best quality result while minimizing cost. LLM providers (Vision/Cloud) validate their own output internally.

- Specific provider names: Use the specified provider if available, otherwise returns `400 Bad Request`.

#### Response

**Status**: `200 OK`

```json
{
  "provider_used": "tesseract",
  "text": "Full extracted text from the image...",
  "blocks": [
    {
      "text": "Example text",
      "bbox": [10.0, 20.0, 100.0, 30.0],
      "confidence": 0.94
    }
  ],
  "meta": {
    "duration_ms": 123.45
  }
}
```

**Response Model**:

| Field | Type | Description |
|-------|------|-------------|
| `provider_used` | `string` | The OCR provider that was actually used |
| `text` | `string` | Full extracted text (concatenated from all blocks) |
| `blocks` | `array` | Array of text blocks with bounding boxes (empty if `return_boxes: false`) |
| `blocks[].text` | `string` | Text content of this block |
| `blocks[].bbox` | `number[4]` | Bounding box as `[x, y, width, height]` in pixels |
| `blocks[].confidence` | `number` | Confidence score between `0.0` and `1.0` |
| `meta` | `object` | Metadata about the processing |
| `meta.duration_ms` | `number` | Processing duration in milliseconds |

**Bounding Box Format**:

The `bbox` array contains four numbers: `[x, y, width, height]`
- `x`: X coordinate of the top-left corner (pixels from left)
- `y`: Y coordinate of the top-left corner (pixels from top)
- `width`: Width of the bounding box (pixels)
- `height`: Height of the bounding box (pixels)

#### Error Responses

**400 Bad Request**:
- Provider is not available or not enabled
- Invalid base64 image data

**422 Unprocessable Entity**:
- Image format is invalid or corrupted
- OCR processing failed (provider-specific error)

**500 Internal Server Error**:
- Unexpected error during OCR processing

#### Example Requests

**Minimal request** (uses defaults):
```bash
curl -X POST http://localhost:5009/v1/ocr \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-App-Id: jarvis-recipes-server" \
  -H "X-Jarvis-App-Key: $JARVIS_APP_KEY" \
  -d '{
    "image": {
      "content_type": "image/png",
      "base64": "iVBORw0KGgoAAAANSUhEUgAA..."
    }
  }'
```

**Full request with all options**:
```bash
curl -X POST http://localhost:5009/v1/ocr \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-App-Id: jarvis-recipes-server" \
  -H "X-Jarvis-App-Key: $JARVIS_APP_KEY" \
  -H "X-Correlation-ID: req-12345" \
  -d '{
    "document_id": "doc-abc123",
    "provider": "auto",
    "image": {
      "content_type": "image/png",
      "base64": "iVBORw0KGgoAAAANSUhEUgAA..."
    },
    "options": {
      "language_hints": ["en", "fr"],
      "return_boxes": true,
      "mode": "document"
    }
  }'
```

**Request with specific provider**:
```bash
curl -X POST http://localhost:5009/v1/ocr \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-App-Id: jarvis-recipes-server" \
  -H "X-Jarvis-App-Key: $JARVIS_APP_KEY" \
  -d '{
    "provider": "apple_vision",
    "image": {
      "content_type": "image/jpeg",
      "base64": "/9j/4AAQSkZJRgABAQAAAQ..."
    },
    "options": {
      "return_boxes": false
    }
  }'
```

#### Example Response

```json
{
  "provider_used": "tesseract",
  "text": "Hello, World!\nThis is a test document.\nMultiple lines of text.",
  "blocks": [
    {
      "text": "Hello,",
      "bbox": [10.0, 5.0, 45.0, 20.0],
      "confidence": 0.98
    },
    {
      "text": "World!",
      "bbox": [60.0, 5.0, 50.0, 20.0],
      "confidence": 0.97
    },
    {
      "text": "This is a test document.",
      "bbox": [10.0, 30.0, 180.0, 20.0],
      "confidence": 0.95
    }
  ],
  "meta": {
    "duration_ms": 234.56
  }
}
```

---

### `POST /v1/ocr/batch`

Perform OCR on multiple images in a single request. This endpoint processes images sequentially and returns results for all images.

#### Request

**Method**: `POST`  
**Path**: `/v1/ocr/batch`  
**Headers**: 
- `Content-Type: application/json` (required)
- `X-Jarvis-App-Id` (required)
- `X-Jarvis-App-Key` (required)
- `X-Correlation-ID` (optional, for request tracking)

**Request Body**:

```json
{
  "document_id": "optional-string",
  "provider": "auto",
  "images": [
    {
      "content_type": "image/png",
      "base64": "iVBORw0KGgoAAAANSUhEUgAA..."
    },
    {
      "content_type": "image/jpeg",
      "base64": "/9j/4AAQSkZJRgABAQAAAQ..."
    }
  ],
  "options": {
    "language_hints": ["en"],
    "return_boxes": true,
    "mode": "document"
  }
}
```

**Request Model**:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `document_id` | `string` | ❌ | `null` | Optional document identifier for tracking |
| `provider` | `string` | ❌ | `"auto"` | OCR provider to use. One of: `"auto"`, `"tesseract"`, `"easyocr"`, `"paddleocr"`, `"apple_vision"`, `"llm_proxy_vision"`, `"llm_proxy_cloud"` |
| `images` | `array` | ✅ | - | Array of images to process (1-100 images) |
| `images[].content_type` | `string` | ✅ | - | MIME type of the image |
| `images[].base64` | `string` | ✅ | - | Base64-encoded image data |
| `options` | `object` | ❌ | See below | OCR processing options (applied to all images) |
| `options.language_hints` | `string[]` | ❌ | `null` | Array of language codes |
| `options.return_boxes` | `boolean` | ❌ | `true` | Whether to return bounding boxes |
| `options.mode` | `string` | ❌ | `"document"` | OCR mode |

**Limits**:
- Maximum 100 images per batch request
- All images are processed with the same provider and options
- Processing is sequential (not parallel)

#### Response

**Status**: `200 OK`

```json
{
  "results": [
    {
      "provider_used": "tesseract",
      "text": "Text from first image...",
      "blocks": [
        {
          "text": "Example",
          "bbox": [10.0, 20.0, 100.0, 30.0],
          "confidence": 0.94
        }
      ],
      "meta": {
        "duration_ms": 123.45
      }
    },
    {
      "provider_used": "tesseract",
      "text": "Text from second image...",
      "blocks": [],
      "meta": {
        "duration_ms": 98.76
      }
    }
  ],
  "meta": {
    "total_images": 2,
    "total_duration_ms": 222.21,
    "provider_used": "tesseract"
  }
}
```

**Response Model**:

| Field | Type | Description |
|-------|------|-------------|
| `results` | `array` | Array of OCR results, one per input image (in same order) |
| `results[].provider_used` | `string` | Provider used for this image |
| `results[].text` | `string` | Extracted text from this image |
| `results[].blocks` | `array` | Text blocks with bounding boxes (same format as single OCR) |
| `results[].meta` | `object` | Metadata for this image |
| `results[].meta.duration_ms` | `number` | Processing duration for this image |
| `meta` | `object` | Batch-level metadata |
| `meta.total_images` | `number` | Total number of images processed |
| `meta.total_duration_ms` | `number` | Total processing duration for all images |
| `meta.provider_used` | `string` | Provider used (same for all images) |

**Error Handling**:

- If any image fails to process, the entire batch fails with `422 Unprocessable Entity`
- Partial results are not returned (all-or-nothing)
- Individual image errors are included in the error response when possible

#### Example Request

```bash
curl -X POST http://localhost:5009/v1/ocr/batch \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-App-Id: jarvis-recipes-server" \
  -H "X-Jarvis-App-Key: $JARVIS_APP_KEY" \
  -d '{
    "provider": "auto",
    "images": [
      {
        "content_type": "image/png",
        "base64": "iVBORw0KGgoAAAANSUhEUgAA..."
      },
      {
        "content_type": "image/jpeg",
        "base64": "/9j/4AAQSkZJRgABAQAAAQ..."
      }
    ],
    "options": {
      "return_boxes": true
    }
  }'
```

---

## File Type Detection

The OCR service supports multiple file types including images and PDFs. Instead of separate endpoints, the service automatically detects file type from the `content_type` header or file content.

### Supported File Types

**Images** (processed directly):
- `image/png`
- `image/jpeg` / `image/jpg`
- `image/gif`
- `image/bmp`
- `image/tiff`
- Other formats supported by PIL/Pillow

**PDFs** (extracted to images first):
- `application/pdf`

When a PDF is submitted:
1. The service extracts pages as images
2. Each page is processed through OCR
3. Results are combined or returned per-page (implementation dependent)

**Request Format for PDFs**:

```json
{
  "image": {
    "content_type": "application/pdf",
    "base64": "JVBERi0xLjQKJeLjz9MK..."
  }
}
```

**Implementation Note**: PDF support will be implemented by:
1. Detecting `application/pdf` content type
2. Extracting pages as images (using pdf2image or similar)
3. Processing each page through OCR
4. Returning combined or per-page results

For now, clients can extract PDF pages to images client-side and use the batch endpoint.

---

## Data Types

### Image Content Types

Supported MIME types:
- `image/png`
- `image/jpeg` or `image/jpg`
- `image/gif`
- `image/bmp`
- `image/tiff`
- Other formats supported by PIL/Pillow

### Base64 Encoding

The `image.base64` field should contain **raw base64-encoded data** without the data URI prefix.

**Correct**:
```json
{
  "base64": "iVBORw0KGgoAAAANSUhEUgAA..."
}
```

**Incorrect** (do not include `data:image/png;base64,` prefix):
```json
{
  "base64": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."
}
```

### Language Hints

Language hints use ISO 639-1 two-letter codes (e.g., `"en"`, `"fr"`, `"de"`, `"es"`). The OCR provider will map these to provider-specific language codes internally.

Common language codes:
- `en` - English
- `fr` - French
- `de` - German
- `es` - Spanish
- `it` - Italian
- `pt` - Portuguese
- `zh` - Chinese
- `ja` - Japanese
- `ko` - Korean

---

## Rate Limiting

Rate limiting is not implemented. This is an internal API protected by Jarvis Auth, so only authorized Jarvis services can access it. Rate limiting is not required.

---

## Request Correlation

Clients can include an optional `X-Correlation-ID` header to track requests across services. This header is logged by the OCR service but does not affect processing.

---

## Provider-Specific Notes

### Tesseract
- Always available (mandatory provider)
- Good general-purpose OCR
- Supports multiple languages
- Moderate accuracy and speed

### EasyOCR
- Must be enabled via `OCR_ENABLE_EASYOCR=true`
- Good for noisy or stylized text
- Higher memory usage
- Slower initialization

### PaddleOCR
- Must be enabled via `OCR_ENABLE_PADDLEOCR=true`
- Strong layout and table detection
- Heavier dependency footprint
- Good for structured documents

### Apple Vision
- Must be enabled via `OCR_ENABLE_APPLE_VISION=true`
- **macOS only** (not available in Docker)
- Highest quality for printed text
- Fastest performance on Apple Silicon
- Best choice when available

### LLM Proxy Vision
- Must be enabled via `OCR_ENABLE_LLM_PROXY_VISION=true`
- Uses local LLM via Jarvis LLM Proxy with "vision" model
- Processes images one at a time (for batch requests)
- Requires `JARVIS_LLM_PROXY_URL`, `JARVIS_APP_ID`, and `JARVIS_APP_KEY` configuration
- Returns JSON format: `{"page1": {"text": "..."}}` for single image, `{"page1": {...}, "page2": {...}}` for batch
- Uses `response_format: {"type": "json_object"}` to enforce JSON output
- Validates output quality internally using LLM proxy "full" model
- Good for complex or stylized text
- Higher latency than traditional OCR

### LLM Proxy Cloud
- Must be enabled via `OCR_ENABLE_LLM_PROXY_CLOUD=true`
- Uses cloud LLM via Jarvis LLM Proxy with "cloud" model
- Processes multiple images in a single request (for batch)
- Each image is sent as a separate message in the content array
- Requires `JARVIS_LLM_PROXY_URL`, `JARVIS_APP_ID`, and `JARVIS_APP_KEY` configuration
- Returns JSON format: `{"page1": {"text": "..."}}` for single image, `{"page1": {...}, "page2": {...}}` for batch
- Uses `response_format: {"type": "json_object"}` to enforce JSON output
- Validates output quality internally using LLM proxy "full" model
- Best for batch processing with cloud models
- Higher latency and cost than local providers

---

## Client Implementation Guidelines

### Error Handling

1. **Always check HTTP status codes** before parsing response body
2. **Handle 401 errors** by checking authentication credentials
3. **Handle 503 errors** by implementing retry logic with exponential backoff
4. **Handle 422 errors** by validating image format before sending
5. **Handle 500 errors** by logging and potentially retrying

### Image Encoding

1. Read image file as binary
2. Encode to base64 (without data URI prefix)
3. Determine MIME type from file extension or magic bytes
4. Include both in request

### Provider Selection

1. Call `GET /v1/providers` on startup to discover available providers
2. Use `"auto"` for best results with automatic validation guardrails:
   - Service tries providers in cost order (cheapest first)
   - Validates output quality using LLM proxy "full" model
   - Automatically falls back to next provider if output is garbled
   - Ensures best quality while minimizing cost
3. Select specific provider if you need guaranteed provider choice
4. Handle `400` errors gracefully if selected provider becomes unavailable

### Output Validation

When using `"auto"` provider selection, the service includes validation guardrails:
- OCR output is validated using LLM proxy "full" model
- Garbled/nonsense text is detected and next provider is tried
- LLM providers (Vision/Cloud) validate their own output internally
- Traditional providers (Tesseract, EasyOCR, etc.) are validated via LLM proxy
- This ensures high-quality results while using the cheapest available provider

### Timeouts

- Recommended request timeout: **30 seconds** for OCR requests
- Recommended connection timeout: **5 seconds**

### Retry Logic

- Retry on `503 Service Unavailable` with exponential backoff
- Do not retry on `400`, `401`, or `422` errors
- Consider retrying `500` errors (may be transient)

---

## Changelog

### Version 1.0.0
- Initial API specification
- Support for Tesseract, EasyOCR, PaddleOCR, Apple Vision, LLM Proxy Vision, and LLM Proxy Cloud providers
- App-to-app authentication via Jarvis Auth
- Health check endpoint (`GET /health`)
- Provider discovery endpoint (`GET /v1/providers`)
- Queue status endpoint (`GET /v1/queue/status`) - monitor Redis queue health
- Single image OCR endpoint (`POST /v1/ocr`)
- Batch OCR endpoint (`POST /v1/ocr/batch`) - supports up to 100 images
- File type detection (images and PDFs via content-type)
- Request compression support (gzip/deflate)
- Bounding boxes and confidence scores
- Redis queue infrastructure with status monitoring
- Automatic provider validation guardrails for quality assurance
- LLM Proxy providers with JSON output format

---

## Queueing and Async Processing

The OCR service is designed to work with **Redis** as a message queue for async processing. Redis runs alongside the OCR service in both native and containerized deployments.

### How Redis Queue Works

**Current State**:
- Redis infrastructure is set up and available
- Queue status endpoint (`GET /v1/queue/status`) is available for monitoring
- Queue job submission endpoints are **not yet implemented** (planned for future)

**Queue Architecture**:
1. **Redis** acts as a message broker using Redis Lists (LPUSH/RPOP pattern)
2. **External workers** consume jobs from Redis and process them
3. **OCR service** provides synchronous endpoints that workers can call
4. Workers can also use provider libraries directly for better performance

**Queue Data Structure**:
- Queue name: `ocr_jobs` (configurable)
- Job format: JSON with OCR request data
- Jobs are stored as Redis List items
- Workers use blocking pop operations (BRPOP) for efficient processing

### Redis Configuration

- **Port**: Configurable via `REDIS_PORT` environment variable (default: `6379`)
- **Host**: 
  - Native: `localhost` (default)
  - Docker: `redis` (service name in docker-compose)
- **Persistence**: Redis data is persisted to a volume in Docker (AOF enabled)

### Deployment

**Docker Compose** (recommended):
- Redis runs as a separate service alongside the OCR service
- Both services are defined in `docker-compose.yml`
- Redis port is configurable via `REDIS_PORT` environment variable
- Services communicate via Docker network

**Native (macOS)**:
- Use `./run.sh` to automatically start Redis in Docker
- Redis runs in a Docker container even when OCR service runs natively
- Use `./run.sh --disable-redis-queue` to skip Redis startup
- OCR service connects to Redis on `localhost`

### Queue Status Endpoint

The `GET /v1/queue/status` endpoint allows external systems to:
- Monitor Redis connectivity
- Check queue length (pending jobs)
- Verify queue infrastructure is healthy
- Track worker activity (if implemented)

This is useful for:
- Health checks and monitoring dashboards
- Alerting when queue is backed up
- Verifying Redis is available before submitting jobs
- Capacity planning

### Queue Architecture Options

**Option 1: External Queue Worker (Current/Recommended)**:
- Separate worker service consumes jobs from Redis
- Worker calls OCR service endpoints (`POST /v1/ocr`) or uses provider libraries directly
- Benefits: 
  - Separation of concerns
  - Scalable workers (run multiple instances)
  - Independent retry logic
  - Can use different languages/frameworks for workers

**Option 2: Built-in Queue Endpoints (Future)**:
- `POST /v1/ocr/queue` - Submit job to queue, returns job ID immediately
- `GET /v1/ocr/jobs/{job_id}` - Get job status and results
- Background workers within OCR service process jobs from Redis
- Benefits: 
  - Single service deployment
  - Simpler for small-scale use cases
  - Built-in job tracking

### Redis Connection

The OCR service reads Redis configuration from environment variables:
- `REDIS_HOST`: Redis hostname (default: `localhost` for native, `redis` for Docker)
- `REDIS_PORT`: Redis port (default: `6379`)

### Queue Job Format (Future)

When queue endpoints are implemented, jobs will be stored in Redis as JSON:

```json
{
  "job_id": "uuid-here",
  "created_at": "2024-01-01T00:00:00Z",
  "status": "pending",
  "request": {
    "document_id": "optional",
    "provider": "auto",
    "image": {
      "content_type": "image/png",
      "base64": "..."
    },
    "options": {
      "language_hints": ["en"],
      "return_boxes": true,
      "mode": "document"
    }
  }
}
```

**Note**: Queue job submission endpoints (`POST /v1/ocr/queue`, `GET /v1/ocr/jobs/{job_id}`) are planned for a future version. Currently, external workers should use the synchronous `POST /v1/ocr` endpoint or implement their own queue consumption logic.

---

## Compression

### Request Compression

**Request compression is supported and recommended** for large images:

- **Benefit**: Significantly smaller payloads = faster uploads, less bandwidth
- **Implementation**: Client compresses JSON payload with gzip/deflate
- **Header**: `Content-Encoding: gzip`
- **Server**: Automatically decompresses gzip/deflate encoded requests
- **Recommendation**: Always use compression for images > 100KB

**Example with compression**:
```bash
# Compress JSON payload and send
gzip -c request.json | curl -X POST http://localhost:5009/v1/ocr \
  -H "Content-Type: application/json" \
  -H "Content-Encoding: gzip" \
  -H "X-Jarvis-App-Id: jarvis-recipes-server" \
  -H "X-Jarvis-App-Key: $JARVIS_APP_KEY" \
  --data-binary @-
```

### Response Compression

Response compression is **not implemented**:

- OCR responses are relatively small (text + metadata)
- Compression overhead would exceed benefits
- JSON responses compress poorly
- Keep responses simple and fast

---

## Future Considerations

- PDF ingestion (extract pages and process automatically)
- Queue job submission endpoints (`POST /v1/ocr/queue`, `GET /v1/ocr/jobs/{job_id}`)
- Webhook support for async processing
- Enhanced layout detection and structured output
- Provider-specific batch optimizations
- Worker health tracking and metrics
- Queue priority levels for urgent jobs

