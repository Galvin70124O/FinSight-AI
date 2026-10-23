import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

# Render sets RENDER=true on its services; ENVIRONMENT=production works anywhere else.
IS_PRODUCTION = os.environ.get("ENVIRONMENT", "").lower() == "production" or bool(os.environ.get("RENDER"))


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    if IS_PRODUCTION:
        raise RuntimeError("SECRET_KEY must be set in production.")
    # Local development only: sessions reset whenever the server restarts.
    return secrets.token_hex(32)


class Config:
    """Application configuration for FinSight AI."""

    SECRET_KEY = _secret_key()
    IS_PRODUCTION = IS_PRODUCTION
    DATABASE_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "instance", "finsight_ai.db"))
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    TRAINED_MODELS_DIR = os.path.join(BASE_DIR, "trained_models")
    DATASET_DIR = os.path.join(BASE_DIR, "dataset")
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    ALLOWED_EXTENSIONS = {"csv"}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    WTF_CSRF_TIME_LIMIT = None
