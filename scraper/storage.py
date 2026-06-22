import csv
import json
import os
from datetime import datetime, timedelta
from core.database import get_mongo_client, get_redis_client, get_database
from core.config import FILES_DIR
from core.logger import get_logger

logger = get_logger("scraper.storage")

class StorageManager:
    def __init__(self, termo_busca, estado_nome, modo_profundo, job_id):
        self.termo_busca = termo_busca
        self.estado_nome = estado_nome
        self.modo_profundo = modo_profundo
        self.job_id = job_id
        
        # Nomes de Arquivos
        self.nome_base_arquivo = os.path.join(FILES_DIR, f"resultados_{termo_busca.replace(' ', '_')}_{estado_nome}")
        self.arquivo_csv = f'{self.nome_base_arquivo}.csv'
        self.arquivo_json = f'{self.nome_base_arquivo}.jsonlines'
        
        # Campos CSV
        self.campos = ['title', 'price', 'location', 'city', 'neighborhood', 'state', 'url', 'list_id', 'date']
        if modo_profundo:
            self.campos += ['description', 'author', 'phone', 'detail_status']

        # Contadores
        self.total_anuncios = 0
        self.total_duplicados = 0
        self.detalhes_coletados = 0
        self.cache_hits = 0
        self.deep_requests = 0
        self.deep_errors = 0
        self.ids_vistos = set()
        self.titulos_vistos = set()

        # Arquivos abertos
        self.f_csv = open(self.arquivo_csv, 'w', newline='', encoding='utf-8-sig')
        self.f_json = open(self.arquivo_json, 'w', encoding='utf-8')
        self.writer = csv.DictWriter(self.f_csv, fieldnames=self.campos, extrasaction='ignore')
        self.writer.writeheader()
        self.f_csv.flush()

        # MongoDB Config
        self.mongo_client = get_mongo_client()
        if self.mongo_client:
            db = get_database()
            self.anuncios_col = db["anuncios"]
            self.extractions_col = db["extractions"]
            self.price_history_col = db["price_history"]
        else:
            self.anuncios_col = None

        # Redis Config
        self.redis_client = get_redis_client()

    def is_duplicate(self, list_id, titulo, preco_str):
        if list_id and list_id in self.ids_vistos:
            self.total_duplicados += 1
            return True
        
        chave_titulo = f"{titulo.strip().lower()}|{preco_str.strip().lower()}"
        if chave_titulo in self.titulos_vistos:
            self.total_duplicados += 1
            return True
            
        if list_id:
            self.ids_vistos.add(list_id)
        self.titulos_vistos.add(chave_titulo)
        return False

    def get_cached_details(self, list_id):
        if not self.anuncios_col or not list_id:
            return None
        doc = self.anuncios_col.find_one({"list_id": list_id}, {"_id": 0, "description": 1, "descricao": 1, "phone": 1, "telefone": 1, "author": 1, "vendedor": 1, "detailsFetchedAt": 1})
        if doc and doc.get("detailsFetchedAt"):
            try:
                fetched_at = datetime.fromisoformat(doc["detailsFetchedAt"])
                if datetime.now() - fetched_at <= timedelta(days=15):
                    return {
                        "description": doc.get("description") or doc.get("descricao") or "",
                        "phone": doc.get("phone") or doc.get("telefone") or "",
                        "author": doc.get("author") or doc.get("vendedor") or "",
                        "detail_status": "cache_hit"
                    }
            except Exception:
                pass
        return None

    def save_anuncio(self, row):
        # Salva em arquivos
        self.writer.writerow(row)
        self.f_csv.flush()
        self.f_json.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.f_json.flush()
        self.total_anuncios += 1

        # Salva no MongoDB
        if self.mongo_client and row.get('list_id'):
            try:
                now = datetime.now()
                doc = row.copy()
                doc["lastSeenAt"] = now.isoformat()
                if self.job_id:
                    doc["lastExtractionId"] = self.job_id

                update_ops = {
                    "$set": doc,
                    "$setOnInsert": {"firstSeenAt": now.isoformat()},
                }
                if self.job_id:
                    update_ops["$addToSet"] = {"extractionIds": self.job_id}

                if row.get("detail_status") == "200":
                    doc["detail_status"] = "ok"
                    update_ops["$set"]["detailsFetchedAt"] = now.isoformat()
                elif row.get("detail_status") and row.get("detail_status") != "cache_hit":
                    doc["detail_status"] = row["detail_status"]

                self.anuncios_col.update_one(
                    {"list_id": row['list_id']},
                    update_ops,
                    upsert=True
                )

                if self.job_id and row.get('price'):
                    self.price_history_col.insert_one({
                        "list_id": row['list_id'],
                        "job_id": self.job_id,
                        "termoBusca": self.termo_busca,
                        "estado": self.estado_nome,
                        "price": row['price'],
                        "observedAt": now.isoformat()
                    })
            except Exception as e:
                logger.error(f"Erro ao salvar no MongoDB: {e}")

    def save_discarded(self, row, motivo):
        """Salva o anúncio descartado para fins de auditoria"""
        if self.mongo_client and row.get('list_id'):
            try:
                now = datetime.now()
                doc = row.copy()
                doc["status"] = "filtered_out"
                doc["motivoDescarte"] = motivo
                doc["lastSeenAt"] = now.isoformat()
                if self.job_id:
                    doc["lastExtractionId"] = self.job_id
                
                self.anuncios_col.update_one(
                    {"list_id": row['list_id']},
                    {"$set": doc, "$setOnInsert": {"firstSeenAt": now.isoformat()}},
                    upsert=True
                )
            except Exception:
                pass

    def update_job_status(self, status_info):
        """Atualiza o documento do job na memória efêmera (Redis)."""
        if self.job_id and self.redis_client:
            try:
                mapping = {
                    "status": status_info.get("status", ""),
                    "progress": str(status_info.get("progress", 0)),
                    "message": status_info.get("message", ""),
                    "totalAnuncios": str(self.total_anuncios),
                    "detalhesColetados": str(self.detalhes_coletados),
                    "duplicadosRemovidos": str(self.total_duplicados)
                }
                
                if mapping["status"] == "completed":
                    return

                key = f"job:{self.job_id}"
                self.redis_client.hset(key, mapping=mapping)
                self.redis_client.expire(key, 86400) # Expira em 24h
            except Exception as e:
                logger.error(f"Erro ao atualizar status no Redis: {e}")

    def close(self):
        self.f_csv.close()
        self.f_json.close()
