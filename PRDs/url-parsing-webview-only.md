# URL Recipe Parsing - Webview-Only Pattern

## Summary

The recipe parsing API has been updated to **always require client-side webview extraction** for URL-based recipe imports. Server-side HTML fetching has been removed due to persistent encoding issues and limitations with modern JavaScript-rendered websites.

This change simplifies the architecture, improves reliability, and ensures consistent recipe extraction across all websites.

## What Changed

### Before
- Client submits URL → Server fetches HTML → Server parses recipe
- Fallback to webview only when server fetch failed (403, encoding errors, etc.)

### After
- Client submits URL → Server validates URL → Returns `next_action="webview_extract"`
- Client opens webview → Extracts JSON-LD/HTML → Submits to `/recipes/parse-payload/async`
- Server parses the extracted content

## API Changes

### `POST /recipes/parse-url/async` (Updated)

**Request:** (unchanged)
```json
{
  "url": "https://example.com/recipe",
  "use_llm_fallback": true
}
```

**Response:** (updated)
```json
{
  "id": "uuid-job-id",
  "status": "PENDING",
  "next_action": "webview_extract",
  "next_action_reason": "webview_required"
}
```

**Important:** This endpoint now **always** returns `next_action="webview_extract"`. The client should **not** poll for job completion. Instead, proceed directly to webview extraction.

### `POST /recipes/parse-payload/async` (Use This Instead)

This is the endpoint clients should use after extracting content from webview.

**Request:**
```json
{
  "input": {
    "source_type": "client_webview",
    "source_url": "https://example.com/recipe",
    "jsonld_blocks": [
      "<script type=\"application/ld+json\">{...}</script>",
      "<script type=\"application/ld+json\">{...}</script>"
    ],
    "html_snippet": "<article>...</article>",
    "extracted_at": "2025-01-15T12:00:00Z",
    "client": "ios:1.2.3"
  }
}
```

**Response:**
```json
{
  "id": "uuid-job-id",
  "status": "PENDING"
}
```

**Then poll:** `GET /recipes/jobs/{job_id}` until `status` is `COMPLETE` or `ERROR`.

## Client Implementation Guide

### Step 1: Validate URL and Get Webview Instruction

```typescript
// Submit URL for validation
const response = await fetch('/recipes/parse-url/async', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    url: userProvidedUrl,
    use_llm_fallback: true
  })
});

const { id, next_action, next_action_reason } = await response.json();

if (next_action === 'webview_extract') {
  // Proceed to webview extraction
  await extractFromWebview(userProvidedUrl);
}
```

### Step 2: Open Webview and Extract Content

```typescript
async function extractFromWebview(url: string) {
  // Open webview (React Native example)
  const webviewRef = useRef<WebView>(null);
  
  // Load the URL
  <WebView
    ref={webviewRef}
    source={{ uri: url }}
    onLoadEnd={() => {
      // Extract JSON-LD blocks
      webviewRef.current?.injectJavaScript(`
        (function() {
          const scripts = document.querySelectorAll('script[type="application/ld+json"]');
          const jsonldBlocks = Array.from(scripts).map(s => s.textContent);
          
          // Extract main content HTML (optional, but helpful for LLM fallback)
          const article = document.querySelector('article') || 
                         document.querySelector('main') || 
                         document.body;
          const htmlSnippet = article ? article.innerHTML.substring(0, 50000) : null;
          
          // Send back to React Native
          window.ReactNativeWebView.postMessage(JSON.stringify({
            jsonld: jsonldBlocks,
            html: htmlSnippet
          }));
        })();
      `);
    }}
    onMessage={(event) => {
      const { jsonld, html } = JSON.parse(event.nativeEvent.data);
      submitExtractedContent(url, jsonld, html);
    }}
  />
}
```

### Step 3: Submit Extracted Content

```typescript
async function submitExtractedContent(
  url: string, 
  jsonldBlocks: string[], 
  htmlSnippet: string | null
) {
  const response = await fetch('/recipes/parse-payload/async', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      input: {
        source_type: 'client_webview',
        source_url: url,
        jsonld_blocks: jsonldBlocks,
        html_snippet: htmlSnippet,
        extracted_at: new Date().toISOString(),
        client: 'ios:1.2.3' // or 'android:1.2.3'
      }
    })
  });

  const { id, status } = await response.json();
  
  // Poll for completion
  await pollJobStatus(id);
}
```

### Step 4: Poll for Job Completion

```typescript
async function pollJobStatus(jobId: string) {
  const maxAttempts = 30;
  const pollInterval = 2000; // 2 seconds
  
  for (let i = 0; i < maxAttempts; i++) {
    const response = await fetch(`/recipes/jobs/${jobId}`, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });
    
    const job = await response.json();
    
    if (job.status === 'COMPLETE') {
      // Success! Use job.result_json.recipe_draft
      return job.result_json.recipe_draft;
    }
    
    if (job.status === 'ERROR') {
      throw new Error(job.error_message || 'Parse failed');
    }
    
    // Still pending/running, wait and retry
    await new Promise(resolve => setTimeout(resolve, pollInterval));
  }
  
  throw new Error('Job timeout');
}
```

## Extraction Best Practices

### JSON-LD Extraction (Preferred)

1. **Extract ALL JSON-LD blocks** - Some pages have multiple blocks (one for Recipe, one for Organization, etc.)
2. **Preserve exact content** - Don't parse/modify the JSON, send raw strings
3. **Handle errors gracefully** - If JSON-LD is malformed, fall back to HTML extraction

```javascript
// Extract all JSON-LD blocks
const scripts = document.querySelectorAll('script[type="application/ld+json"]');
const jsonldBlocks = Array.from(scripts)
  .map(script => script.textContent)
  .filter(content => content && content.trim().length > 0);
```

### HTML Snippet Extraction (Fallback)

1. **Extract main content** - Look for `<article>`, `<main>`, or recipe-specific containers
2. **Limit size** - Keep under 50KB to avoid payload size issues
3. **Include structure** - Preserve HTML structure, not just text

```javascript
// Find main content container
const article = document.querySelector('article[itemtype*="Recipe"]') ||
                document.querySelector('article') ||
                document.querySelector('main') ||
                document.body;

// Extract HTML (limit size)
const htmlSnippet = article ? 
  article.innerHTML.substring(0, 50000) : 
  null;
```

## Error Handling

### URL Validation Errors

If `POST /recipes/parse-url/async` returns an error (400):
- `error_code: "invalid_url"` - URL format is invalid
- `error_code: "fetch_failed"` - Site is unreachable
- `error_code: "unsupported_content_type"` - Not an HTML page

**Action:** Show error to user, don't proceed to webview.

### Webview Extraction Errors

If extraction fails in webview:
- **No JSON-LD found** - Still submit with `html_snippet` only
- **Page won't load** - Show error, allow user to retry
- **JavaScript disabled** - Fall back to HTML extraction only

### Job Processing Errors

If job status is `ERROR`:
- Check `error_code` and `error_message`
- Common codes: `parse_failed`, `llm_failed`, `invalid_payload`
- Show user-friendly error message
- Optionally allow manual recipe entry

## Migration Checklist

- [ ] Update client to check for `next_action="webview_extract"` in parse-url response
- [ ] Implement webview extraction (JSON-LD + HTML)
- [ ] Update to use `/recipes/parse-payload/async` instead of polling parse-url job
- [ ] Update polling to use `/recipes/jobs/{job_id}` endpoint
- [ ] Handle extraction errors gracefully
- [ ] Test with various recipe sites
- [ ] Update error handling UI
- [ ] Remove any server-side fetch retry logic

## Benefits

1. **No encoding issues** - Browser handles encoding correctly
2. **Works with JavaScript sites** - Webview executes JS, server fetch doesn't
3. **Better anti-bot handling** - Browser context avoids many blocking mechanisms
4. **Simpler server code** - No complex encoding/decoding logic
5. **More reliable** - Browser is the source of truth for page content

## Backwards Compatibility

**Breaking Change:** Clients that relied on server-side fetching will need to be updated. The `next_action` field is now always present and should be respected.

Clients that ignore `next_action` will receive a job that never completes (since no server-side processing happens).

## Testing

Test with various recipe sites:
- Simple sites with JSON-LD (should work perfectly)
- Sites with only HTML (should use LLM fallback)
- JavaScript-rendered sites (should work via webview)
- Sites with multiple JSON-LD blocks (should extract all)
- Sites with encoding issues (should work via browser)

## Example: Complete Flow

```typescript
// 1. User enters URL
const url = "https://tastesbetterfromscratch.com/chicken-pot-pie/";

// 2. Validate URL
const validateResponse = await fetch('/recipes/parse-url/async', {
  method: 'POST',
  body: JSON.stringify({ url })
});
const { next_action } = await validateResponse.json();

// 3. Extract from webview
if (next_action === 'webview_extract') {
  const { jsonld, html } = await extractFromWebview(url);
  
  // 4. Submit extracted content
  const submitResponse = await fetch('/recipes/parse-payload/async', {
    method: 'POST',
    body: JSON.stringify({
      input: {
        source_type: 'client_webview',
        source_url: url,
        jsonld_blocks: jsonld,
        html_snippet: html
      }
    })
  });
  const { id } = await submitResponse.json();
  
  // 5. Poll for result
  const recipe = await pollJobStatus(id);
  
  // 6. Display recipe to user
  showRecipePreview(recipe);
}
```

