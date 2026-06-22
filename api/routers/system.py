import os
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from core.database import check_mongo, check_redis
from core.config import LOGS_DIR

router = APIRouter()

# =============================================================================
# HEALTH CHECK
# =============================================================================
@router.get("/")
@router.get("/health")
@router.get("/api/health")
def health_check():
    mongo_ok = check_mongo()
    redis_ok = check_redis()

    return {
        "status": "ok" if redis_ok and mongo_ok else "degraded",
        "mongo": "connected" if mongo_ok else "disconnected",
        "redis": "connected" if redis_ok else "disconnected",
        "service": "olx-extractor-api"
    }

# =============================================================================
# LOG DE ERROS DO CLIENTE (Frontend)
# =============================================================================
@router.post("/client-errors")
@router.post("/api/client-errors")
def log_client_error(payload: dict):
    """Registra erros do frontend no arquivo client_errors.log"""
    try:
        log_path = os.path.join(LOGS_DIR, "client_errors.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except: 
        pass
    return {"status": "logged"}

# =============================================================================
# DOWNLOAD DE ARQUIVOS
# =============================================================================
@router.get("/download/{filename}")
@router.get("/api/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return FileResponse(path=file_path, filename=filename, media_type='application/octet-stream')
