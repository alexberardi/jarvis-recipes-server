from typing import Optional

from pydantic import BaseModel


class CurrentUser(BaseModel):
    id: int
    email: Optional[str] = None

