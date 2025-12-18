from typing import Optional

from pydantic import BaseModel, ConfigDict


class StockIngredientRead(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class StockUnitRead(BaseModel):
    id: int
    name: str
    abbreviation: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

