from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ImageRef(BaseModel):
    filename: str
    content_type: Optional[str] = None
    data_base64: str  # base64-encoded bytes


class IngestionInput(BaseModel):
    source_type: Literal["server_fetch", "client_webview", "image_upload"]
    source_url: Optional[str] = None
    jsonld_blocks: Optional[List[str]] = Field(default=None)
    html_snippet: Optional[str] = None
    extracted_at: Optional[str] = None
    client: Optional[str] = None
    images: Optional[List[ImageRef]] = None

