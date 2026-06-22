import os

# Ambientes Locais Padrões
MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "olx_extractor")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

FILES_DIR = os.getenv("FILES_DIR", "arquivos_gerados")
LOGS_DIR = os.getenv("LOGS_DIR", "logs")

OLX_DETAIL_WORKERS = int(os.getenv("OLX_DETAIL_WORKERS", "3"))
OLX_DETAIL_CACHE_DAYS = int(os.getenv("OLX_DETAIL_CACHE_DAYS", "15"))

# Cria os diretórios necessários ao carregar a config
os.makedirs(FILES_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
