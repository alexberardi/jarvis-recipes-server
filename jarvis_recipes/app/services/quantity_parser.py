from decimal import Decimal, InvalidOperation
from typing import Optional


def parse_quantity_display(raw: Optional[str]) -> Optional[Decimal]:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    # Whole or decimal numbers
    try:
        if "/" not in value and " " not in value:
            return Decimal(value)
    except InvalidOperation:
        pass

    # Fractions like "1/2" or "1 1/2"
    try:
        if " " in value:
            whole_part, frac_part = value.split(" ", 1)
            whole = Decimal(whole_part)
            num_str, denom_str = frac_part.split("/", 1)
            num = Decimal(num_str)
            denom = Decimal(denom_str)
            if denom == 0:
                return None
            return whole + (num / denom)
        if "/" in value:
            num_str, denom_str = value.split("/", 1)
            num = Decimal(num_str)
            denom = Decimal(denom_str)
            if denom == 0:
                return None
            return num / denom
    except (InvalidOperation, ValueError):
        return None

    return None

