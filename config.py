import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    """Application configuration for FinSight AI."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "fin-sight-ai-dev-key")
    DATABASE_PATH = os.path.join(BASE_DIR, "instance", "finsight_ai.db")
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    TRAINED_MODELS_DIR = os.path.join(BASE_DIR, "trained_models")
    DATASET_DIR = os.path.join(BASE_DIR, "dataset")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_EXTENSIONS = {"csv"}
