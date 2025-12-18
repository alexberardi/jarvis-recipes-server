from jarvis_recipes.app.db import models


def seed_stock(db_session):
    db_session.add_all(
        [
            models.StockIngredient(name="chicken breast"),
            models.StockIngredient(name="onion"),
        ]
    )
    db_session.add_all(
        [
            models.StockUnitOfMeasure(name="tablespoon", abbreviation="tbsp"),
            models.StockUnitOfMeasure(name="teaspoon", abbreviation="tsp"),
        ]
    )
    db_session.commit()


def test_stock_ingredients_search(client, db_session, user_token):
    seed_stock(db_session)
    resp = client.get("/ingredients/stock?q=chicken", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert any("chicken" in item["name"] for item in body)

    resp = client.get("/ingredients/stock?limit=1", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_stock_units_search(client, db_session, user_token):
    seed_stock(db_session)
    resp = client.get("/units/stock?q=tbsp", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert any(item["abbreviation"] == "tbsp" for item in body)


def test_stock_requires_auth(client):
    resp = client.get("/ingredients/stock")
    assert resp.status_code == 401

