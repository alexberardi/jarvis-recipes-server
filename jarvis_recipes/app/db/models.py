from datetime import datetime
import enum

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Boolean,
    func,
)
from sqlalchemy.orm import relationship

from jarvis_recipes.app.db.base import Base


class Setting(Base):
    """Settings table for runtime configuration with multi-tenant support."""

    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), nullable=False, index=True)
    value = Column(Text, nullable=True)  # JSON-encoded value
    value_type = Column(String(50), nullable=False, default="string")
    category = Column(String(100), nullable=False, default="general", index=True)
    description = Column(Text, nullable=True)
    requires_reload = Column(Boolean, default=False)
    is_secret = Column(Boolean, default=False)
    env_fallback = Column(String(255), nullable=True)

    # Multi-tenant scoping (all nullable = system default)
    household_id = Column(String(255), nullable=True, index=True)
    node_id = Column(String(255), nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "key", "household_id", "node_id", "user_id", name="uq_setting_scope"
        ),
    )


class SourceType(str, enum.Enum):
    MANUAL = "manual"
    IMAGE = "image"
    URL = "url"


recipe_tags = Table(
    "recipe_tags",
    Base.metadata,
    Column("recipe_id", ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True, index=True)

    recipes = relationship("Recipe", back_populates="user", cascade="all, delete-orphan")
    meal_plans = relationship("MealPlan", back_populates="user", cascade="all, delete-orphan")


class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(Integer, primary_key=True, index=True)
    # user_id is retained as AUTHORSHIP -- who added it -- while household_id is
    # VISIBILITY. Keeping both means "added by Casey" survives, and the migration
    # stays reversible: dropping household_id restores per-user scoping exactly.
    user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    # Nullable on purpose. Rows created before the migration have no household
    # until the backfill runs, and this service cannot resolve a user's household
    # on its own -- that lives in jarvis-auth. Until then they stay visible to
    # their author via the NULL fallback in services/scoping.py.
    household_id = Column(String(255), nullable=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    image_url = Column(String)
    source_type = Column(Enum(SourceType, native_enum=False), nullable=False, default=SourceType.MANUAL)
    source_url = Column(String)
    servings = Column(Integer)
    total_time_minutes = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="recipes")
    ingredients = relationship("Ingredient", back_populates="recipe", cascade="all, delete-orphan")
    steps = relationship(
        "Step",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="Step.step_number",
    )
    tags = relationship("Tag", secondary=recipe_tags, back_populates="recipes")
    plan_items = relationship("MealPlanItem", back_populates="recipe")


class Ingredient(Base):
    __tablename__ = "ingredients"

    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    quantity_display = Column(String)
    quantity_value = Column(Numeric(10, 4))
    unit = Column(String)

    recipe = relationship("Recipe", back_populates="ingredients")


class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("recipe_id", "step_number", name="uq_step_order"),)

    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)

    recipe = relationship("Recipe", back_populates="steps")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True, index=True)

    recipes = relationship("Recipe", secondary=recipe_tags, back_populates="tags")


class MealPlan(Base):
    __tablename__ = "meal_plans"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    # See Recipe.household_id -- same authorship/visibility split.
    household_id = Column(String(255), nullable=True, index=True)
    name = Column(String)
    start_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="meal_plans")
    items = relationship("MealPlanItem", back_populates="meal_plan", cascade="all, delete-orphan")


class MealPlanItem(Base):
    __tablename__ = "meal_plan_items"

    id = Column(Integer, primary_key=True)
    meal_plan_id = Column(Integer, ForeignKey("meal_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    meal_type = Column(String, nullable=False)

    meal_plan = relationship("MealPlan", back_populates="items")
    recipe = relationship("Recipe", back_populates="plan_items")


class GrocerySkuMap(Base):
    """What this household buys when a recipe says "ground beef".

    A shopping preference, not inventory -- which is why it lives here and not in
    jarvis-pantry. Keyed on the NORMALIZED ingredient name so one row covers every
    recipe's phrasing of the same thing.
    """

    __tablename__ = "grocery_sku_map"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    # See Recipe.household_id -- same authorship/visibility split.
    household_id = Column(String(255), nullable=True, index=True)
    retailer = Column(String(32), nullable=False, default="walmart")
    ingredient_name = Column(String(255), nullable=False)
    sku = Column(String(64), nullable=False)
    product_name = Column(String(512), nullable=True)
    unit_size = Column(String(64), nullable=True)
    # "manual" beats "llm": a manual row is the household telling us what they
    # buy, and a background guess must never overwrite that.
    source = Column(String(16), nullable=False, default="manual")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "household_id",
            "user_id",
            "retailer",
            "ingredient_name",
            name="ux_grocery_sku_map_scope",
        ),
    )


class RecipeParseJob(Base):
    __tablename__ = "recipe_parse_jobs"

    id = Column(String, primary_key=True, index=True)
    job_type = Column(String, nullable=False)  # e.g., "url", "ocr", "social"
    url = Column(String, nullable=True)
    use_llm_fallback = Column(Boolean, nullable=False, default=True)
    job_data = Column(JSON, nullable=True)  # minimal payload (e.g., ingestion_id)
    status = Column(String, nullable=False, default="PENDING")
    result_json = Column(JSON)
    error_code = Column(String)
    error_message = Column(Text)
    attempts = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    user_id = Column(String, nullable=False, index=True)
    # The queue worker runs without a token, long after the request that started
    # the job, so the household has to travel on the row. See the migration note.
    household_id = Column(String(255), nullable=True, index=True)
    committed_at = Column(DateTime)
    abandoned_at = Column(DateTime)
    canceled_at = Column(DateTime)

    __table_args__ = (
        Index("ix_recipe_parse_jobs_user_status", "user_id", "status"),
        Index("ix_recipe_parse_jobs_completed_at", "completed_at"),
    )


class RecipeIngestion(Base):
    __tablename__ = "recipe_ingestions"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    image_s3_keys = Column(JSON, nullable=False)
    selected_tier = Column(Integer, nullable=True)
    tier1_text = Column(Text, nullable=True)
    tier2_text = Column(Text, nullable=True)
    tier3_raw_response = Column(JSON, nullable=True)
    pipeline_json = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="PENDING")
    tier_max = Column(Integer, nullable=True)
    title_hint = Column(String, nullable=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True)
    # OCR ensemble join. One image goes to every OCR host, so the readings
    # accumulate here until they can be handed to the LLM together. See
    # services/ocr_join.
    ocr_readings = Column(JSON, nullable=True)
    ocr_expected = Column(Integer, nullable=True)
    ocr_joined_at = Column(DateTime, nullable=True)

    user = relationship("User")
    recipe = relationship("Recipe")


class MailboxMessage(Base):
    __tablename__ = "mailbox_messages"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    user = relationship("User")


class StageRecipe(Base):
    __tablename__ = "stage_recipes"

    # Integer, like every other recipe id here. It used to be a UUID string, and
    # because staged and committed ids flow through the same fields
    # (Selection.recipe_id, the mobile plan state, exclude_recipe_ids) that
    # mismatch caused three separate bugs -- see migration f6a7b8c9d0e1.
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    yield_text = Column(String)
    prep_time_minutes = Column(Integer, default=0)
    cook_time_minutes = Column(Integer, default=0)
    ingredients = Column(JSON, nullable=False)
    steps = Column(JSON, nullable=False)
    tags = Column(JSON, nullable=False, default=list)
    notes = Column(JSON, nullable=False, default=list)
    request_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)

    user = relationship("User")


class StockIngredient(Base):
    __tablename__ = "stock_ingredients"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True, index=True)
    category = Column(String)
    synonyms = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockUnitOfMeasure(Base):
    __tablename__ = "stock_units_of_measure"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    abbreviation = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

