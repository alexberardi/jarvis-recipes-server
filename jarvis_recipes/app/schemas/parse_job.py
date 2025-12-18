from typing import Optional

from pydantic import AnyHttpUrl, BaseModel, Field


class ParseJobCreate(BaseModel):
    url: AnyHttpUrl
    use_llm_fallback: bool = True


class ParseJobStatus(BaseModel):
    job_id: str = Field(alias="id")
    status: str
    result: Optional[dict] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    class Config:
        populate_by_name = True

