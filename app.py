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
    db = mongo_client["olx_extractor"]
    extractions_col = db["extractions"]
except Exception as e:
    print(f"Erro ao conectar no MongoDB em app.py: {e}")
    mongo_client = None

class ExtractRequest(BaseModel):
    termoBusca: str
    estado: str
    paginasBusca: int = 5
    modoProfundo: bool = True
    limiteDetalhes: int = 50

def run_extraction_task(req: ExtractRequest, job_id: str):
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
                "data": datetime.now().isoformat()
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
        if mongo_client:
            extractions_col.update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "completed",
                    "progress": 100,
                    "message": "Extração concluída com sucesso",
                    "csvFile": csv_file,
                    "excelFile": excel_file,
                    "downloadUrl": f"/api/download/{excel_file}"
                }}
            )
            
    except Exception as e:
        if mongo_client:
            extractions_col.update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "error",
                    "message": f"Erro durante a extração: {str(e)}"
                }}
            )

@app.get("/")
@app.get("/health")
@app.get("/api")
@app.get("/api/")
@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "API está online!"}

@app.post("/extract")
@app.post("/api/extract")
def start_extraction(req: ExtractRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    
    if mongo_client:
        extractions_col.insert_one({
            "job_id": job_id,
            "status": "queued",
            "message": "Extração iniciada",
            "progress": 0,
            "data": datetime.now().isoformat()
        })
        
    background_tasks.add_task(run_extraction_task, req, job_id)
    
    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Extração iniciada"
    }

@app.get("/extract/status/{job_id}")
@app.get("/api/extract/status/{job_id}")
def get_status(job_id: str):
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")
        
    job = extractions_col.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
        
    if job.get("status") == "completed":
        anuncios_list = list(db["anuncios"].find({"job_id": job_id}, {"_id": 0}))
        for a in anuncios_list:
            if "_captured_at" in a:
                a["_captured_at"] = a["_captured_at"].isoformat()
        job["anuncios"] = anuncios_list
        
    return job

@app.get("/download/{filename}")
@app.get("/api/download/{filename}")
def download_excel(filename: str):
    file_path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
        
    return FileResponse(path=file_path, filename=filename, media_type='application/octet-stream')

@app.get("/extractions")
@app.get("/api/extractions")
def list_extractions():
    """Lista o histórico de todas as extrações realizadas."""
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")
    
    jobs = list(extractions_col.find({}, {"_id": 0}).sort("data", -1).limit(50))
    return {"extractions": jobs}

@app.get("/extractions/latest")
@app.get("/api/extractions/latest")
def get_latest_extraction():
    """Retorna a última extração concluída com a lista completa de anúncios."""
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")
    
    job = extractions_col.find_one(
        {"status": "completed"},
        {"_id": 0},
        sort=[("data", -1)]
    )
    
    if not job:
        raise HTTPException(status_code=404, detail="Nenhuma extração concluída encontrada")
    
    anuncios_list = list(db["anuncios"].find({"job_id": job["job_id"]}, {"_id": 0}))
    for a in anuncios_list:
        if "_captured_at" in a:
            a["_captured_at"] = a["_captured_at"].isoformat()
    job["anuncios"] = anuncios_list
    
    return job

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=5000, reload=True)
