import uuid
from typing import Any, Dict

from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models


def publish(db: Session, user_id: str, msg_type: str, payload: Dict[str, Any]) -> models.MailboxMessage:
    message = models.MailboxMessage(
        id=str(uuid.uuid4()),
        user_id=str(user_id),
        type=msg_type,
        payload=payload,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message

