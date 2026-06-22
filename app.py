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
from services.correlation_service import gerar_dossie_correlacionado

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

    # Cria índice textual para buscas correlacionadas (idempotente)
    try:
        anuncios_col.create_index([("title", "text"), ("description", "text")], name="idx_text_busca")
    except Exception:
        pass  # Índice já existe ou erro não-crítico

except Exception as e:
    print(f"Erro ao conectar no MongoDB em app.py: {e}")
    mongo_client = None
    mongo_connected = False

# Conexão Redis
try:
    import redis
    redis_client = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
    redis_client.ping()
    redis_connected = True
except Exception as e:
    print(f"Erro ao conectar no Redis em app.py: {e}")
    redis_client = None
    redis_connected = False

class ExtractRequest(BaseModel):
    termoBusca: str
    estado: str
    paginasBusca: int = 5
    modoProfundo: bool = True
    limiteDetalhes: int = 50
    pegarTodosDetalhes: bool = False


def _serialize_datetime(doc):
    """Converte campos datetime para ISO string em um documento."""
    for key, val in doc.items():
        if isinstance(val, datetime):
            doc[key] = val.isoformat()
    return doc


def _get_anuncios_for_job(job_id: str):
    """Busca anúncios de um job específico e serializa para o formato esperado pelo frontend."""
    docs = list(anuncios_col.find({"extractionIds": job_id}, {"_id": 0}))
    for d in docs:
        _serialize_datetime(d)
        
        # Normalização para camelCase exigida pelo frontend
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

    if redis_client:
        redis_client.hset(f"job:{job_id}", mapping={
            "status": "preparing",
            "progress": "10",
            "message": "Preparando sessão e parâmetros da busca..."
        })

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
                "limiteDetalhes": limite,
                "pegarTodosDetalhes": req.pegarTodosDetalhes,
                "createdAt": now.isoformat()
            }},
            upsert=True
        )

    try:
        # 1. Roda o scraper
        result = scrape_olx(
            termo_busca=req.termoBusca,
            estado=req.estado,
            paginas_busca=req.paginasBusca,
            modo_profundo=req.modoProfundo,
            limite_detalhes=limite,
            job_id=job_id,
            redis_conn=redis_client
        )
        csv_file = result.get("csv_file")

        # 2. Atualiza status para geração se não foi cancelado
        is_cancelled = result.get("cancelled", False)
        
        if not is_cancelled:
            if redis_client:
                redis_client.hset(f"job:{job_id}", mapping={
                    "status": "generating",
                    "progress": "90",
                    "message": "Gerando arquivos CSV compatíveis com Excel..."
                })
                
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
        
        # Apaga o cache efêmero para forçar leitura do Mongo
        if redis_client:
            redis_client.delete(f"job:{job_id}")

        if mongo_client:
            status_final = "cancelled" if is_cancelled else "completed"
            msg_final = "Extração cancelada pelo usuário" if is_cancelled else "Extração concluída com sucesso"
            
            extractions_col.update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": status_final,
                    "progress": 100,
                    "message": msg_final,
                    "totalAnuncios": result.get("totalAnuncios", 0),
                    "detalhesColetados": result.get("detalhesColetados", 0),
                    "duplicadosRemovidos": result.get("duplicadosRemovidos", 0),
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
    # Checagem dinâmica
    redis_ok = False
    if redis_client:
        try:
            redis_ok = redis_client.ping()
        except:
            pass
            
    mongo_ok = False
    if mongo_client:
        try:
            mongo_client.admin.command('ping')
            mongo_ok = True
        except:
            pass

    return {
        "status": "ok" if redis_ok and mongo_ok else "degraded",
        "mongo": "connected" if mongo_ok else "disconnected",
        "redis": "connected" if redis_ok else "disconnected",
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
        redis_client.expire(key, 86400) # Expira em 24h

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
# RE-EXECUTAR EXTRAÇÃO
# =============================================================================
@app.post("/extract/{job_id}/retry")
@app.post("/api/extract/{job_id}/retry")
def retry_extraction(job_id: str, background_tasks: BackgroundTasks):
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB offline")
    job = extractions_col.find_one({"job_id": job_id}, {"_id": 0})
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


# =============================================================================
# CANCELAR EXTRAÇÃO
# =============================================================================
@app.post("/extract/{job_id}/cancel")
@app.post("/api/extract/{job_id}/cancel")
def cancel_extraction(job_id: str):
    if redis_client:
        redis_client.setex(f"job:{job_id}:cancel", 86400, "1")
        return {"job_id": job_id, "status": "cancelling", "message": "Cancelamento solicitado"}
    return {"error": "Redis indisponível"}


# =============================================================================
# CONSULTAR STATUS DE TODOS OS JOBS ATIVOS (Redis)
# =============================================================================
@app.get("/extract/status")
@app.get("/api/extract/status")
def list_active_statuses():
    """Retorna a lista de todos os jobs ativos no Redis."""
    if not redis_client:
        return []

    keys = redis_client.keys("job:*:status") if False else redis_client.keys("job:*")
    # Filtra chaves que contêm exatamente 2 partes (job:{id}) para evitar outras chaves
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


# =============================================================================
# CONSULTAR STATUS DE UM JOB ESPECÍFICO
# =============================================================================
@app.get("/extract/status/{job_id}")
@app.get("/api/extract/status/{job_id}")
def get_status(job_id: str):
    # 1. Tenta pegar o status efêmero do Redis (tempo real)
    if redis_client:
        cached_job = redis_client.hgetall(f"job:{job_id}")
        if cached_job and cached_job.get("status"):
            # Cast the integer fields so Lovable frontend receives numbers, not strings
            cached_job["progress"] = int(cached_job.get("progress", 0))
            cached_job["totalAnuncios"] = int(cached_job.get("totalAnuncios", 0))
            cached_job["detalhesColetados"] = int(cached_job.get("detalhesColetados", 0))
            cached_job["duplicadosRemovidos"] = int(cached_job.get("duplicadosRemovidos", 0))
            cached_job["job_id"] = job_id
            return cached_job

    # 2. Se não estiver no Redis, ou se não houver Redis conectado, busca no MongoDB (fonte definitiva)
    if not mongo_client:
        raise HTTPException(status_code=500, detail="Nenhum banco de dados conectado")

    job = extractions_col.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    # Quando finalizado ou cancelado, inclui a lista completa de anúncios
    if job.get("status") in ["completed", "cancelled"]:
        job["anuncios"] = _get_anuncios_for_job(job_id)

    return job


# =============================================================================
# LISTAR HISTÓRICO DE EXTRAÇÕES
# =============================================================================
@app.get("/extractions")
@app.get("/api/extractions")
def list_extractions(skip: int = 0, limit: int = 50):
    """Retorna o histórico das últimas extrações com paginação."""
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    limit = min(limit, 200)
    total = extractions_col.count_documents({})
    jobs = list(extractions_col.find({}, {"_id": 0}).sort("createdAt", -1).skip(skip).limit(limit))
    
    for j in jobs:
        # Indica ao frontend se existem anúncios disponíveis para essa extração
        j["hasAnuncios"] = anuncios_col.count_documents({"extractionIds": j.get("job_id")}) > 0
        
    return {
        "items": jobs,
        "total": total,
        "skip": skip,
        "limit": limit
    }


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
# MOTOR DE CORRELAÇÃO E CONSOLIDAÇÃO (Sem IA)
# =============================================================================
@app.get("/anuncios/correlacionados")
@app.get("/api/anuncios/correlacionados")
def correlacionar_anuncios(
    termo: str,
    estado: str = None,
    preco_min: float = None,
    preco_max: float = None,
    excluir_termos: str = None,
    _termos: str = None
):
    """
    Busca, filtra, agrupa e consolida todos os anúncios do banco
    relacionados ao termo pesquisado. Retorna um dossiê JSON completo.
    A IA NÃO participa dessa etapa — tudo é feito via código.
    """
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    # Converte string CSV de termos extras em lista
    termos_extras = None
    termos_raw = excluir_termos or _termos
    if termos_raw:
        termos_extras = [t.strip() for t in termos_raw.split(",") if t.strip()]

    dossie = gerar_dossie_correlacionado(
        anuncios_col=anuncios_col,
        price_history_col=price_history_col,
        termo=termo,
        estado=estado,
        preco_min=preco_min,
        preco_max=preco_max,
        excluir_termos_extras=termos_extras
    )

    return dossie


# =============================================================================
# DELETAR EXTRAÇÃO DO HISTÓRICO
# =============================================================================
@app.delete("/extractions/{job_id}")
@app.delete("/api/extractions/{job_id}")
def delete_extraction(job_id: str):
    """Exclui um job do banco de dados e remove suas referências."""
    if not mongo_client:
        raise HTTPException(status_code=500, detail="MongoDB offline")
        
    # Remove do histórico
    extractions_col.delete_one({"job_id": job_id})
    
    # Remove a tag de extractionIds de todos os anúncios associados
    anuncios_col.update_many({"extractionIds": job_id}, {"$pull": {"extractionIds": job_id}})
    # Apaga anúncios que ficaram órfãos (não pertencem a nenhuma extração)
    anuncios_col.delete_many({"extractionIds": {"$size": 0}})
    
    # Remove do histórico de preços
    price_history_col.delete_many({"job_id": job_id})
    
    # Remove qualquer vestígio do Redis
    if redis_client:
        redis_client.delete(f"job:{job_id}")
        redis_client.delete(f"job:{job_id}:cancel")
        
    return {"status": "deleted", "message": "Extração removida com sucesso"}


# =============================================================================
# LOG DE ERROS DO CLIENTE (Frontend)
# =============================================================================
@app.post("/client-errors")
@app.post("/api/client-errors")
def log_client_error(payload: dict):
    """Registra erros do frontend no arquivo client_errors.log"""
    try:
        import json
        with open("client_errors.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except: 
        pass
    return {"status": "logged"}


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
