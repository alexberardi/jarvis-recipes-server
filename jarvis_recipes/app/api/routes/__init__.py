import importlib

from fastapi import APIRouter

from jarvis_recipes.app.api.routes import planner
from jarvis_recipes.app.api.routes import recipes, stock, tags, from_image, ingestion, meal_plans

import_routes = importlib.import_module("jarvis_recipes.app.api.routes.import")

api_router = APIRouter()
api_router.include_router(recipes.router)
api_router.include_router(ingestion.router)
api_router.include_router(tags.router)
api_router.include_router(import_routes.router)
api_router.include_router(planner.router)
api_router.include_router(stock.router)
api_router.include_router(from_image.router)
api_router.include_router(meal_plans.router)

