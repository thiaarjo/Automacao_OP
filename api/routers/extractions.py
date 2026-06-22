import uuid
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from core.database import get_database, get_redis_client
from scraper.engine import scrape_olx
from gerar_excel import gerar_excel

router = APIRouter()

class ExtractRequest(BaseModel):
    termoBusca: str
    estado: str
    paginasBusca: int = 5
    modoProfundo: bool = True
    limiteDetalhes: int = 50
    pegarTodosDetalhes: bool = False

def _serialize_datetime(doc):
    for key, val in doc.items():
        if isinstance(val, datetime):
            doc[key] = val.isoformat()
    return doc

def _get_anuncios_for_job(job_id: str, db):
    anuncios_col = db["anuncios"]
    docs = list(anuncios_col.find({"extractionIds": job_id}, {"_id": 0}))
    for d in docs:
        _serialize_datetime(d)
        if "list_id" in d:
            d["listId"] = d.pop("list_id")
        if "detail_status" in d:
            d["detailStatus"] = d.pop("detail_status")
        if "author" in d:
            d["vendedor"] = d.pop("author")
        if "phone" in d:
            d["telefone"] = d.pop("phone")
        if "description" in d:
            d["descricao"] = d.pop("description")
    return docs

def run_extraction_task(req: ExtractRequest, job_id: str):
    now = datetime.now()
    limite = 999999 if req.pegarTodosDetalhes else req.limiteDetalhes
    db = get_database()
    redis_client = get_redis_client()
    
    if redis_client:
        redis_client.hset(f"job:{job_id}", mapping={
            "status": "preparing",
            "progress": "10",
            "message": "Preparando sessão e parâmetros da busca..."
        })

    if db is not None:
        extractions_col = db["extractions"]
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
                "limiteDetalhes": limite,
                "pegarTodosDetalhes": req.pegarTodosDetalhes,
                "createdAt": now.isoformat()
            }},
            upsert=True
        )

    try:
        result = scrape_olx(
            termo_busca=req.termoBusca,
            estado=req.estado,
            paginas_busca=req.paginasBusca,
            modo_profundo=req.modoProfundo,
            limite_detalhes=limite,
            job_id=job_id
        )
        csv_file = result.get("csv_file")
        is_cancelled = result.get("cancelled", False)
        
        if not is_cancelled:
            if redis_client:
                redis_client.hset(f"job:{job_id}", mapping={
                    "status": "generating",
                    "progress": "90",
                    "message": "Gerando arquivos CSV compatíveis com Excel..."
                })
                
            if db is not None:
                db["extractions"].update_one(
                    {"job_id": job_id},
                    {"$set": {
                        "status": "generating",
                        "progress": 90,
                        "message": "Gerando arquivos CSV compatíveis com Excel..."
                    }}
                )

        excel_file = gerar_excel(csv_file)
        finished = datetime.now()
        
        if redis_client:
            redis_client.delete(f"job:{job_id}")

        if db is not None:
            status_final = "cancelled" if is_cancelled else "completed"
            msg_final = "Extração cancelada pelo usuário" if is_cancelled else "Extração concluída com sucesso"
            
            db["extractions"].update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": status_final,
                    "progress": 100,
                    "message": msg_final,
                    "totalAnuncios": result.get("totalAnuncios", 0),
                    "totalBasicosColetados": result.get("totalBasicosColetados", 0),
                    "anunciosDescartadosPeloFiltro": result.get("anunciosDescartadosPeloFiltro", 0),
                    "anunciosValidos": result.get("anunciosValidos", 0),
                    "detalhesColetados": result.get("detalhesColetados", 0),
                    "duplicadosRemovidos": result.get("duplicadosRemovidos", 0),
                    "cacheHits": result.get("cacheHits", 0),
                    "deepRequests": result.get("deepRequests", 0),
                    "deepErrors": result.get("deepErrors", 0),
                    "csvFile": csv_file,
                    "excelFile": excel_file,
                    "downloadUrl": f"/api/download/{excel_file}",
                    "finishedAt": finished.isoformat()
                }}
            )

    except Exception as e:
        if redis_client:
            redis_client.hset(f"job:{job_id}", mapping={
                "status": "error",
                "message": f"Erro: {str(e)}"
            })
            
        if db is not None:
            db["extractions"].update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "error",
                    "message": f"Erro durante a extração: {str(e)}",
                    "finishedAt": datetime.now().isoformat()
                }}
            )

@router.post("/extract")
@router.post("/api/extract")
def start_extraction(req: ExtractRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    now = datetime.now()
    redis_client = get_redis_client()
    db = get_database()

    if redis_client:
        key = f"job:{job_id}"
        redis_client.hset(key, mapping={
            "status": "queued",
            "progress": "0",
            "message": "Extração iniciada",
            "totalAnuncios": "0",
            "detalhesColetados": "0",
            "duplicadosRemovidos": "0"
        })
        redis_client.expire(key, 86400)

    if db is not None:
        db["extractions"].insert_one({
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

    return {"job_id": job_id, "status": "queued", "message": "Extração iniciada"}

@router.post("/extract/{job_id}/retry")
@router.post("/api/extract/{job_id}/retry")
def retry_extraction(job_id: str, background_tasks: BackgroundTasks):
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="MongoDB offline")
    
    job = db["extractions"].find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job original não encontrado")
    
    req = ExtractRequest(
        termoBusca=job.get("termoBusca", ""),
        estado=job.get("estado", ""),
        paginasBusca=job.get("paginasBusca", 5),
        modoProfundo=job.get("modoProfundo", True),
        limiteDetalhes=job.get("limiteDetalhes", 50),
        pegarTodosDetalhes=job.get("pegarTodosDetalhes", False)
    )
    return start_extraction(req, background_tasks)

@router.post("/extract/{job_id}/cancel")
@router.post("/api/extract/{job_id}/cancel")
def cancel_extraction(job_id: str):
    redis_client = get_redis_client()
    if redis_client:
        redis_client.setex(f"job:{job_id}:cancel", 86400, "1")
        return {"job_id": job_id, "status": "cancelling", "message": "Cancelamento solicitado"}
    return {"error": "Redis indisponível"}

@router.get("/extract/status")
@router.get("/api/extract/status")
def list_active_statuses():
    redis_client = get_redis_client()
    if not redis_client:
        return []

    keys = redis_client.keys("job:*")
    job_keys = [k for k in keys if len(k.split(":")) == 2]
    
    active_jobs = []
    for k in job_keys:
        cached_job = redis_client.hgetall(k)
        if cached_job and cached_job.get("status"):
            job_id = k.split(":")[1]
            status = cached_job.get("status")
            if status not in ["completed", "error"]:
                cached_job["progress"] = int(cached_job.get("progress", 0))
                cached_job["totalAnuncios"] = int(cached_job.get("totalAnuncios", 0))
                cached_job["detalhesColetados"] = int(cached_job.get("detalhesColetados", 0))
                cached_job["duplicadosRemovidos"] = int(cached_job.get("duplicadosRemovidos", 0))
                cached_job["job_id"] = job_id
                active_jobs.append(cached_job)

    return active_jobs

@router.get("/extract/status/{job_id}")
@router.get("/api/extract/status/{job_id}")
def get_status(job_id: str):
    redis_client = get_redis_client()
    if redis_client:
        cached_job = redis_client.hgetall(f"job:{job_id}")
        if cached_job and cached_job.get("status"):
            cached_job["progress"] = int(cached_job.get("progress", 0))
            cached_job["totalAnuncios"] = int(cached_job.get("totalAnuncios", 0))
            cached_job["detalhesColetados"] = int(cached_job.get("detalhesColetados", 0))
            cached_job["duplicadosRemovidos"] = int(cached_job.get("duplicadosRemovidos", 0))
            cached_job["job_id"] = job_id
            return cached_job

    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="Nenhum banco de dados conectado")

    job = db["extractions"].find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    if job.get("status") in ["completed", "cancelled"]:
        job["anuncios"] = _get_anuncios_for_job(job_id, db)

    return job

@router.get("/extractions")
@router.get("/api/extractions")
def list_extractions(skip: int = 0, limit: int = 50):
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    limit = min(limit, 200)
    total = db["extractions"].count_documents({})
    jobs = list(db["extractions"].find({}, {"_id": 0}).sort("createdAt", -1).skip(skip).limit(limit))
    
    for j in jobs:
        j["hasAnuncios"] = db["anuncios"].count_documents({"extractionIds": j.get("job_id")}) > 0
        
    return {"items": jobs, "total": total, "skip": skip, "limit": limit}

@router.get("/extractions/latest")
@router.get("/api/extractions/latest")
def get_latest_extraction():
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    job = db["extractions"].find_one(
        {"status": "completed"},
        {"_id": 0},
        sort=[("createdAt", -1)]
    )

    if not job:
        raise HTTPException(status_code=404, detail="Nenhuma extração concluída encontrada")

    job["anuncios"] = _get_anuncios_for_job(job["job_id"], db)
    return job

@router.get("/extractions/{job_id}/anuncios")
@router.get("/api/extractions/{job_id}/anuncios")
def get_extraction_anuncios(job_id: str):
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    job = db["extractions"].find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Extração não encontrada")

    return _get_anuncios_for_job(job_id, db)

@router.delete("/extractions/{job_id}")
@router.delete("/api/extractions/{job_id}")
def delete_extraction(job_id: str):
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="MongoDB offline")
        
    db["extractions"].delete_one({"job_id": job_id})
    db["anuncios"].update_many({"extractionIds": job_id}, {"$pull": {"extractionIds": job_id}})
    db["anuncios"].delete_many({"extractionIds": {"$size": 0}})
    db["price_history"].delete_many({"job_id": job_id})
    
    redis_client = get_redis_client()
    if redis_client:
        redis_client.delete(f"job:{job_id}")
        redis_client.delete(f"job:{job_id}:cancel")
        
    return {"status": "deleted", "message": "Extração removida com sucesso"}
