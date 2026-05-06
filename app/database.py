from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = "sqlite:///./hoops_dynasty.db"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    migrate_rating_color_columns()


def get_session():
    with Session(engine) as session:
        yield session


def migrate_rating_color_columns():
    color_columns = [
        "athleticism_color",
        "speed_color",
        "rebounding_color",
        "defense_color",
        "shot_blocking_color",
        "low_post_color",
        "perimeter_color",
        "ball_handling_color",
        "passing_color",
        "work_ethic_color",
        "stamina_color",
        "durability_color",
        "free_throw_color",
    ]

    with engine.begin() as conn:
        try:
            existing = conn.execute(text("PRAGMA table_info(playerratingsnapshot)")).fetchall()
        except Exception:
            return

        existing_names = {row[1] for row in existing}
        if not existing_names:
            return

        for col in color_columns:
            if col not in existing_names:
                conn.execute(text(f"ALTER TABLE playerratingsnapshot ADD COLUMN {col} VARCHAR DEFAULT ''"))
