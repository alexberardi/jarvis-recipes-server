import asyncio
import json

import pytest
from fastapi.exceptions import RequestValidationError

from jarvis_recipes.app.main import validation_exception_handler


@pytest.mark.asyncio
async def test_validation_handler_formats_errors():
    exc = RequestValidationError(
        errors=[
            {"loc": ("body", "url"), "msg": "field required"},
            {"loc": ("query", "page"), "msg": "value is not a valid integer"},
        ]
    )
    response = await validation_exception_handler(None, exc)
    assert response.status_code == 422
    body = json.loads(response.body)
    assert body["error_code"] == "validation_error"
    assert body["message"] == "Invalid request payload."
    assert "job_id" in body and body["job_id"]
    assert {"field": "body.url", "message": "field required"} in body["details"]
    assert {"field": "query.page", "message": "value is not a valid integer"} in body["details"]

