import os
from cachelib.file import FileSystemCache
from dotenv import load_dotenv

# Load environment variables from .env file at project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
env_path = os.path.join(project_root, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)


class Config:
    """Application configuration class."""

    # Project paths
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    PROJECT_ROOT = project_root

    # Secret key for sessions and cryptographic signing
    SECRET_KEY = os.environ.get("SECRET_KEY", "fallback-secret-key-mad2-2026")

    # Database configuration (SQLite)
    DB_NAME = os.environ.get("SQLITE_DB_NAME", "examination.db")
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, DB_NAME)}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Flask-Session configuration using CacheLib FileSystemCache
    session_dir = os.path.join(BASE_DIR, "flask_session")
    os.makedirs(session_dir, exist_ok=True)

    SESSION_TYPE = "cachelib"
    SESSION_CACHELIB = FileSystemCache(cache_dir=session_dir, threshold=1000)
    SESSION_PERMANENT = False

    # Initial Admin credentials loaded strictly from environment variables
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@emp.local")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@123")

    # Redis and Celery Configuration
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL)

    # Redis Application Caching
    CACHE_REDIS_URL = os.environ.get("CACHE_REDIS_URL", REDIS_URL)
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get("CACHE_DEFAULT_TIMEOUT", 60))

    # Secure Directory for Asynchronous CSV Exports
    EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
    os.makedirs(EXPORTS_DIR, exist_ok=True)

    # SMTP Email Configuration for Real Notifications
    SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 1025))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "false").lower() in ("true", "1")
    SMTP_SENDER = os.environ.get("SMTP_SENDER", "noreply@emp.local")
