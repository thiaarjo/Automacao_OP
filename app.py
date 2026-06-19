import os
import uuid
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pymongo import MongoClient

from olx import scrape_olx
from gerar_excel import gerar_excel

app = FastAPI(title="OLX Extractor API")

# Configuração CORS para permitir o front-end Lovable
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexão MongoDB
try:
    mongo_client = MongoClient("mongodb://127.0.0.1:27017/", serverSelectionTimeoutMS=2000)
    mongo_client.admin.command("ping")  # Testa conexão real
    db = mongo_client["olx_extractor"]
    extractions_col = db["extractions"]
    anuncios_col = db["anuncios"]
    price_history_col = db["price_history"]
    mongo_connected = True
except Exception as e:
    print(f"Erro ao conectar no MongoDB em app.py: {e}")
    mongo_client = None
    mongo_connected = False

class ExtractRequest(BaseModel):
    termoBusca: str
    estado: str
    paginasBusca: int = 5
    modoProfundo: bool = True
    limiteDetalhes: int = 50


def _serialize_datetime(doc):
    """Converte campos datetime para ISO string em um documento."""
    for key, val in doc.items():
        if isinstance(val, datetime):
            doc[key] = val.isoformat()
    return doc


def _get_anuncios_for_job(job_id: str):
    """Busca anúncios de um job específico e serializa."""
    docs = list(anuncios_col.find({"extractionIds": job_id}, {"_id": 0}))
    for d in docs:
        _serialize_datetime(d)
    return docs


def run_extraction_task(req: ExtractRequest, job_id: str):
    now = datetime.now()

    if mongo_client:
        extractions_col.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "preparing",
                "progress": 10,
                "message": "Preparando sessão e parâmetros da busca...",
                "termoBusca": req.termoBusca,
                "estado": req.estado,
                "paginasBusca": req.paginasBusca,
                "modoProfundo": req.modoProfundo,
                "limiteDetalhes": req.limiteDetalhes,
                "createdAt": now.isoformat()
            }},
            upsert=True
        )

    try:
        # 1. Roda o scraper
        csv_file = scrape_olx(
            termo_busca=req.termoBusca,
            estado=req.estado,
            paginas_busca=req.paginasBusca,
            modo_profundo=req.modoProfundo,
            limite_detalhes=req.limiteDetalhes,
            job_id=job_id
        )

        # 2. Atualiza status para geração
        if mongo_client:
            extractions_col.update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "generating",
                    "progress": 90,
                    "message": "Gerando arquivos CSV compatíveis com Excel..."
                }}
            )

        # 3. Gera o Excel
        excel_file = gerar_excel(csv_file)

        # 4. Finaliza o Job
        finished = datetime.now()
        if mongo_client:
            extractions_col.update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "completed",
                    "progress": 100,
                    "message": "Extração concluída com sucesso",
                    "csvFile": csv_file,
                    "excelFile": excel_file,
                    "downloadUrl": f"/api/download/{excel_file}",
                    "finishedAt": finished.isoformat()
                }}
            )

    except Exception as e:
        if mongo_client:
            extractions_col.update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "error",
                    "message": f"Erro durante a extração: {str(e)}",
                    "finishedAt": datetime.now().isoformat()
                }}
            )


# =============================================================================
# HEALTH CHECK
# =============================================================================
@app.get("/")
@app.get("/health")
@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "mongo": "connected" if mongo_connected else "disconnected",
        "service": "olx-extractor-api"
    }


# =============================================================================
# CRIAR EXTRAÇÃO
# =============================================================================
@app.post("/extract")
@app.post("/api/extract")
def start_extraction(req: ExtractRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    now = datetime.now()

    if mongo_client:
        extractions_col.insert_one({
            "job_id": job_id,
            "status": "queued",
            "message": "Extração iniciada",
            "progress": 0,
            "termoBusca": req.termoBusca,
            "estado": req.estado,
            "paginasBusca": req.paginasBusca,
            "modoProfundo": req.modoProfundo,
            "limiteDetalhes": req.limiteDetalhes,
            "createdAt": now.isoformat()
        })

    background_tasks.add_task(run_extraction_task, req, job_id)

    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Extração iniciada"
    }


# =============================================================================
# CONSULTAR STATUS DE UM JOB
# =============================================================================
@app.get("/extract/status/{job_id}")
@app.get("/api/extract/status/{job_id}")
def get_status(job_id: str):
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    job = extractions_col.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    # Quando finalizado, inclui a lista completa de anúncios
    if job.get("status") == "completed":
        job["anuncios"] = _get_anuncios_for_job(job_id)

    return job


# =============================================================================
# LISTAR HISTÓRICO DE EXTRAÇÕES
# =============================================================================
@app.get("/extractions")
@app.get("/api/extractions")
def list_extractions():
    """Retorna o histórico das últimas 50 extrações."""
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    jobs = list(extractions_col.find({}, {"_id": 0}).sort("createdAt", -1).limit(50))
    for j in jobs:
        # Indica ao frontend se existem anúncios disponíveis para essa extração
        j["hasAnuncios"] = anuncios_col.count_documents({"extractionIds": j.get("job_id")}) > 0
    return jobs


# =============================================================================
# CARREGAR ÚLTIMA EXTRAÇÃO CONCLUÍDA
# =============================================================================
@app.get("/extractions/latest")
@app.get("/api/extractions/latest")
def get_latest_extraction():
    """Retorna a última extração concluída com a lista completa de anúncios."""
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    job = extractions_col.find_one(
        {"status": "completed"},
        {"_id": 0},
        sort=[("createdAt", -1)]
    )

    if not job:
        raise HTTPException(status_code=404, detail="Nenhuma extração concluída encontrada")

    job["anuncios"] = _get_anuncios_for_job(job["job_id"])

    return job


# =============================================================================
# BUSCAR ANÚNCIOS DE UMA EXTRAÇÃO ESPECÍFICA
# =============================================================================
@app.get("/extractions/{job_id}/anuncios")
@app.get("/api/extractions/{job_id}/anuncios")
def get_extraction_anuncios(job_id: str):
    """Retorna os anúncios de uma extração específica."""
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    # Verifica se o job existe
    job = extractions_col.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Extração não encontrada")

    return _get_anuncios_for_job(job_id)


# =============================================================================
# DOWNLOAD DE ARQUIVOS
# =============================================================================
@app.get("/download/{filename}")
@app.get("/api/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return FileResponse(path=file_path, filename=filename, media_type='application/octet-stream')


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=5000, reload=True)
