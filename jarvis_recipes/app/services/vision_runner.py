import argparse
import asyncio
import json
import sys
from pathlib import Path

from jarvis_recipes.app.services import llm_client


async def main():
    parser = argparse.ArgumentParser(description="Single-image vision runner")
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--image-path", required=True)
    parser.add_argument("--payload-json", required=True)
    args = parser.parse_args()

    image_path = Path(args.image_path)
    payload = json.loads(args.payload_json)
    with image_path.open("rb") as f:
        image_bytes = f.read()

    draft, warnings = await llm_client.call_vision_single(
        image=image_bytes,
        model_name=args.model_name,
        current_draft=payload.get("current_draft") or {},
        image_index=payload.get("image_index") or 1,
        image_count=payload.get("image_count") or 1,
        is_final_image=bool(payload.get("is_final_image")),
        title_hint=payload.get("title_hint"),
        timeout_seconds=args.timeout_seconds,
    )
    # stdout must be JSON only
    print(json.dumps({"draft": draft.model_dump(), "warnings": warnings or []}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)

