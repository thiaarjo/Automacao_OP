import time
import random
import urllib.parse
import concurrent.futures
from curl_cffi import requests
from bs4 import BeautifulSoup
from core.logger import get_logger
from core.config import OLX_DETAIL_WORKERS
from scraper.parser import OlxParser
from scraper.storage import StorageManager
from services.correlation_service import aplicar_termos_negativos, remover_outliers

logger = get_logger("scraper.engine")

class OlxScraper:
    def __init__(self, termo_busca, estado, paginas_busca, modo_profundo, limite_detalhes, job_id=None):
        self.termo_busca = termo_busca
        self.estado = estado
        self.paginas_busca = paginas_busca
        self.modo_profundo = modo_profundo
        self.limite_detalhes = limite_detalhes
        self.job_id = job_id
        
        self.session = requests.Session(impersonate="chrome120")
        
        # Formata URL base
        termo_url = urllib.parse.quote(termo_busca)
        estado_url = estado.lower()
        if estado_url in ['brasil', 'br', '']:
            self.url_base_template = f'https://www.olx.com.br/brasil?q={termo_url}&o={{pagina}}'
            estado_nome = 'brasil'
        else:
            self.url_base_template = f'https://www.olx.com.br/estado-{estado_url}?q={termo_url}&o={{pagina}}'
            estado_nome = estado_url

        self.storage = StorageManager(termo_busca, estado_nome, modo_profundo, job_id)

    def check_cancel(self):
        """Verifica se o usuário requisitou o cancelamento deste job."""
        if self.storage.redis_client and self.job_id:
            try:
                if self.storage.redis_client.get(f"job:{self.job_id}:cancel"):
                    return True
            except:
                pass
        return False

    def run(self):
        logger.info(f"Iniciando raspagem para '{self.termo_busca}' no estado '{self.estado}'")
        
        paginas_vazias = 0
        is_cancelled = False
        todos_basicos = []
        total_basicos = 0
        total_descartados = 0
        total_validos = 0

        try:
            # ====================================================================
            # FASE 1: Coleta Rápida (Apenas Básicos)
            # ====================================================================
            for i in range(1, self.paginas_busca + 1):
                if self.check_cancel():
                    logger.warning("Cancelamento detectado. Interrompendo paginação.")
                    is_cancelled = True
                    break

                url = self.url_base_template.format(pagina=i)
                logger.info(f"[BUSCA] Raspando página {i} de {self.paginas_busca}...")
                
                progresso_atual = int((i / self.paginas_busca) * 30)
                self.storage.update_job_status({
                    "status": "scraping",
                    "progress": progresso_atual,
                    "message": f"Coletando anúncios básicos: página {i} de {self.paginas_busca}"
                })

                try:
                    response = self.session.get(url, timeout=15)
                    if response.status_code != 200:
                        logger.warning(f"Página {i} retornou status {response.status_code}")
                        if response.status_code == 403:
                            time.sleep(random.uniform(5, 10))
                        continue

                    soup = BeautifulSoup(response.text, 'html.parser')
                    script_tag = soup.find('script', id='__NEXT_DATA__')
                    
                    if not script_tag:
                        paginas_vazias += 1
                        continue

                    anuncios = OlxParser.parse_next_data(script_tag.string)
                    if not anuncios:
                        paginas_vazias += 1
                        if paginas_vazias >= 3:
                            break
                        continue

                    paginas_vazias = 0

                    for anuncio in anuncios:
                        if not anuncio: continue

                        titulo = anuncio.get('subject') or anuncio.get('title')
                        if not titulo: continue

                        list_id = OlxParser.safe_str(anuncio.get('listId') or anuncio.get('list_id'))
                        preco_raw = OlxParser.safe_str(anuncio.get('price'))
                        preco_num = OlxParser.parse_price(preco_raw)

                        if self.storage.is_duplicate(list_id, titulo, preco_raw):
                            continue

                        location_raw = anuncio.get('location')
                        location_str, cidade, bairro = OlxParser.extract_location(location_raw)
                        estado_anuncio = OlxParser.get_state_from_ddd(location_str)
                        if not estado_anuncio:
                            estado_anuncio = self.estado

                        data_raw = OlxParser.safe_str(anuncio.get('date'))
                        data_iso = OlxParser.parse_date(data_raw)

                        row = {
                            'title': OlxParser.safe_str(titulo),
                            'price': preco_num if preco_num is not None else 0,
                            'priceRaw': preco_raw,
                            'location': location_str,
                            'city': cidade,
                            'neighborhood': bairro,
                            'state': estado_anuncio,
                            'url': OlxParser.safe_str(anuncio.get('url')),
                            'list_id': list_id,
                            'date': data_iso,
                            'dateRaw': data_raw,
                        }
                        todos_basicos.append(row)

                    time.sleep(random.uniform(0.5, 1.5))

                except Exception as e:
                    logger.error(f"Erro na página {i}: {e}")
                    time.sleep(random.uniform(2, 5))
                    
                if is_cancelled:
                    break

            total_basicos = len(todos_basicos)

            # ====================================================================
            # FASE 2: Limpeza e Inteligência (Motor de Correlação)
            # ====================================================================
            anuncios_validos = []
            if not is_cancelled:
                self.storage.update_job_status({
                    "status": "filtering",
                    "progress": 35,
                    "message": "Analisando anúncios e removendo falsos positivos..."
                })
                
                anuncios_sem_termos, removidos_termos = aplicar_termos_negativos(todos_basicos)
                for r in removidos_termos:
                    self.storage.save_discarded(r, "termo_negativo")
                    
                anuncios_validos, outliers = remover_outliers(anuncios_sem_termos)
                for o in outliers:
                    self.storage.save_discarded(o, o.get("motivoRemocao", "outlier_preco"))

                total_descartados = len(removidos_termos) + len(outliers)
                total_validos = len(anuncios_validos)
                
                self.storage.update_job_status({
                    "status": "filtered",
                    "progress": 40,
                    "message": f"Filtro aplicado: {total_basicos} coletados, {total_descartados} descartados, {total_validos} válidos"
                })

            # ====================================================================
            # FASE 3: Modo Profundo Inteligente (Apenas nos Válidos)
            # ====================================================================
            if self.modo_profundo and anuncios_validos and not is_cancelled:
                if self.check_cancel():
                    logger.warning("Cancelamento detectado antes dos detalhes.")
                    is_cancelled = True
                else:
                    tarefas_profundas = []
                    for idx, row in enumerate(anuncios_validos):
                        pode_buscar = (self.limite_detalhes is None or self.storage.detalhes_coletados < self.limite_detalhes)
                        if not pode_buscar or not row['url']:
                            row.update({'description': '', 'author': '', 'phone': '', 'detail_status': 'pulado' if not pode_buscar else 'sem_url'})
                            continue
                        
                        cached = self.storage.get_cached_details(row['list_id'])
                        if cached:
                            row.update(cached)
                            self.storage.cache_hits += 1
                            self.storage.detalhes_coletados += 1
                        else:
                            tarefas_profundas.append(row)

                    if tarefas_profundas:
                        logger.info(f"Buscando detalhes de {len(tarefas_profundas)} anúncios validados...")
                        self.storage.update_job_status({
                            "status": "details",
                            "progress": 50,
                            "message": f"Buscando detalhes profundos: {len(tarefas_profundas)} de {total_validos} (Cache: {self.storage.cache_hits})"
                        })

                        block_flag = False

                        def fetch_worker(anuncio_row):
                            nonlocal block_flag
                            if self.check_cancel() or block_flag:
                                return {'description': '', 'author': '', 'phone': '', 'detail_status': 'blocked_or_cancelled'}
                            
                            try:
                                sess = requests.Session(impersonate="chrome120")
                                resp = sess.get(anuncio_row['url'], timeout=15)
                                
                                if resp.status_code in [403, 429]:
                                    block_flag = True
                                    return {'description': '', 'author': '', 'phone': '', 'detail_status': 'blocked_or_rate_limited'}
                                if resp.status_code != 200:
                                    return {'description': '', 'author': '', 'phone': '', 'detail_status': str(resp.status_code)}
                                    
                                return OlxParser.parse_deep_details(resp.text)
                            except Exception as e:
                                return {'description': '', 'author': '', 'phone': '', 'detail_status': 'error'}

                        with concurrent.futures.ThreadPoolExecutor(max_workers=OLX_DETAIL_WORKERS) as executor:
                            futures = {executor.submit(fetch_worker, row): row for row in tarefas_profundas}
                            concluidos = 0
                            total_tarefas = len(tarefas_profundas)
                            
                            for future in concurrent.futures.as_completed(futures):
                                row = futures[future]
                                try:
                                    resultado = future.result()
                                    row.update(resultado)
                                    
                                    if resultado.get('detail_status') in ['200', 'ok']:
                                        self.storage.detalhes_coletados += 1
                                        self.storage.deep_requests += 1
                                    elif resultado.get('detail_status') == 'error':
                                        self.storage.deep_errors += 1
                                except Exception:
                                    row.update({'description': '', 'author': '', 'phone': '', 'detail_status': 'error'})
                                    self.storage.deep_errors += 1
                                    
                                concluidos += 1
                                if concluidos % 5 == 0 or concluidos == total_tarefas:
                                    pct = 50 + int((concluidos / total_tarefas) * 40)
                                    self.storage.update_job_status({
                                        "status": "details",
                                        "progress": min(pct, 90),
                                        "message": f"Processando detalhes: {concluidos}/{total_tarefas} (Cache: {self.storage.cache_hits})"
                                    })

                        time.sleep(random.uniform(2, 5))

            # ====================================================================
            # FASE 4: Persistência Final (Somente Válidos)
            # ====================================================================
            if not is_cancelled:
                self.storage.update_job_status({
                    "status": "saving",
                    "progress": 90,
                    "message": "Gerando CSV, JSONL e persistindo no banco..."
                })
                for row in anuncios_validos:
                    self.storage.save_anuncio(row)

        finally:
            self.storage.close()

        # Status Final
        if is_cancelled:
            self.storage.update_job_status({"status": "cancelled", "progress": 100, "message": "Extração cancelada pelo usuário"})
            logger.warning("Extração finalizada com status: Cancelado")
        else:
            self.storage.update_job_status({"progress": 100, "message": "Gerando CSV, JSONL e persistindo no banco..."})
            logger.info("Extração finalizada com status: Sucesso")
            
        return {
            "csv_file": self.storage.arquivo_csv,
            "jsonl_file": self.storage.arquivo_json,
            "totalBasicosColetados": total_basicos,
            "anunciosDescartadosPeloFiltro": total_descartados,
            "anunciosValidos": total_validos,
            "totalAnuncios": self.storage.total_anuncios,
            "detalhesColetados": self.storage.detalhes_coletados,
            "duplicadosRemovidos": self.storage.total_duplicados,
            "cacheHits": self.storage.cache_hits,
            "deepRequests": self.storage.deep_requests,
            "deepErrors": self.storage.deep_errors,
            "cancelled": is_cancelled
        }

def scrape_olx(termo_busca, estado, paginas_busca=5, modo_profundo=True, limite_detalhes=20, job_id=None):
    """
    Wrapper público simplificado para rodar o scraper.
    Não precisa passar as credenciais de banco pois a engine puxa do config.py via database.py.
    """
    scraper = OlxScraper(
        termo_busca=termo_busca,
        estado=estado,
        paginas_busca=paginas_busca,
        modo_profundo=modo_profundo,
        limite_detalhes=limite_detalhes,
        job_id=job_id
    )
    
    return scraper.run()
