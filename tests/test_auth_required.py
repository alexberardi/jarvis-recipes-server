def test_auth_required_missing_header(client):
    response = client.get("/recipes")
    assert response.status_code == 401
    assert response.json()["detail"] in {"Not authenticated", "Invalid or expired token"}


def test_auth_invalid_token(client):
    response = client.get("/recipes", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"

