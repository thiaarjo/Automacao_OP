from pymongo import MongoClient
import redis
from core.config import MONGO_URI, MONGO_DB_NAME, REDIS_URL
from core.logger import get_logger

logger = get_logger("core.database")

# Clientes Singleton
_mongo_client = None
_redis_client = None

def get_mongo_client() -> MongoClient:
    global _mongo_client
    if _mongo_client is None:
        try:
            _mongo_client = MongoClient(MONGO_URI)
            # Testa a conexão rapidinho
            _mongo_client.admin.command('ping')
            logger.info("Conectado ao MongoDB com sucesso.")
        except Exception as e:
            logger.error(f"Erro ao conectar ao MongoDB: {e}")
    return _mongo_client

def get_database():
    client = get_mongo_client()
    if client:
        return client[MONGO_DB_NAME]
    return None

def get_redis_client():
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            _redis_client.ping()
            logger.info("Conectado ao Redis com sucesso.")
        except Exception as e:
            logger.error(f"Erro ao conectar ao Redis: {e}")
            _redis_client = None
    return _redis_client

def check_mongo() -> bool:
    client = get_mongo_client()
    if client:
        try:
            client.admin.command('ping')
            return True
        except Exception:
            return False
    return False

def check_redis() -> bool:
    client = get_redis_client()
    if client:
        try:
            return client.ping()
        except Exception:
            return False
    return False
