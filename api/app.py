from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.config import CORS_ORIGINS
from core.logger import get_logger
from api.routers import system, extractions, correlation

logger = get_logger("api.app")

app = FastAPI(title="OLX Extractor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrando as rotas modulares
app.include_router(system.router)
app.include_router(extractions.router)
app.include_router(correlation.router)

logger.info("OLX Extractor API iniciada com sucesso.")
