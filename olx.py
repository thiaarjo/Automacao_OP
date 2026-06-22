from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import csv
import time
import random
import re
import urllib.parse
from datetime import datetime, timedelta
import concurrent.futures
from pymongo import MongoClient
from services.correlation_service import aplicar_termos_negativos, remover_outliers
try:
    import redis
except ImportError:
    redis = None

# ============================================================================
# CONSTANTES GLOBAIS
# ============================================================================
STATES_DDD = {
    "99": "Maranhão", "98": "Maranhão",
    "97": "Amazonas", "96": "Amapá",
    "95": "Roraima",
    "94": "Pará", "93": "Pará", "92": "Amazonas", "91": "Pará",
    "89": "Piauí", "88": "Ceará",
    "87": "Pernambuco", "86": "Piauí", "85": "Ceará",
    "84": "Rio Grande do Norte", "83": "Paraíba",
    "82": "Alagoas", "81": "Pernambuco",
    "79": "Sergipe",
    "77": "Bahia", "75": "Bahia", "74": "Bahia", "73": "Bahia", "71": "Bahia",
    "69": "Rondônia", "68": "Acre",
    "67": "Mato Grosso do Sul",
    "66": "Mato Grosso", "65": "Mato Grosso",
    "64": "Goiás", "63": "Tocantins", "62": "Goiás",
    "61": "Distrito Federal",
    "55": "Rio Grande do Sul", "54": "Rio Grande do Sul",
    "53": "Rio Grande do Sul", "51": "Rio Grande do Sul",
    "49": "Santa Catarina", "48": "Santa Catarina", "47": "Santa Catarina",
    "46": "Paraná", "45": "Paraná", "44": "Paraná",
    "43": "Paraná", "42": "Paraná", "41": "Paraná",
    "38": "Minas Gerais", "37": "Minas Gerais", "35": "Minas Gerais",
    "34": "Minas Gerais", "33": "Minas Gerais", "32": "Minas Gerais",
    "31": "Minas Gerais",
    "28": "Espírito Santo", "27": "Espírito Santo",
    "24": "Rio de Janeiro", "22": "Rio de Janeiro", "21": "Rio de Janeiro",
    "19": "São Paulo", "18": "São Paulo", "17": "São Paulo",
    "16": "São Paulo", "15": "São Paulo", "14": "São Paulo",
    "13": "São Paulo", "12": "São Paulo", "11": "São Paulo",
}

# ============================================================================
# CLASSE 1: PARSER (Tratamento e limpeza de dados)
# ============================================================================
class OlxParser:
    @staticmethod
    def safe_str(value):
        """Garante que qualquer valor seja salvo como texto para evitar erros de formatação."""
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @staticmethod
    def extract_location(location_raw):
        """Extrai cidade e bairro e formata a localização de forma legível."""
        if location_raw is None:
            return '', '', ''

        if isinstance(location_raw, str):
            partes = location_raw.split(',')
            cidade = partes[0].strip() if partes else ''
            bairro = ''
            if len(partes) > 1:
                bairro_parte = partes[1].split(' - ')[0].strip()
                bairro = bairro_parte
            return location_raw, cidade, bairro

        if isinstance(location_raw, dict):
            cidade = location_raw.get('municipality', '')
            bairro = location_raw.get('neighbourhood', '')
            uf = location_raw.get('uf', '')
            ddd = location_raw.get('ddd', '')

            partes = []
            if cidade: partes.append(cidade)
            if bairro: partes.append(bairro)
            location_str = ', '.join(partes)
            if ddd: location_str += f' - DDD {ddd}'
            elif uf: location_str += f' - {uf}'

            return location_str, cidade, bairro

        return OlxParser.safe_str(location_raw), '', ''

    @staticmethod
    def get_state_from_ddd(location_str):
        """Tenta adivinhar o estado pelo DDD ou UF na string de localização."""
        match_uf = re.search(r'\s-\s*([A-Za-z]{2})(?:\s|$)', location_str)
        if match_uf:
            uf = match_uf.group(1).lower()
            return uf

        match_ddd = re.search(r'DDD\s*(\d{2})', location_str)
        if match_ddd:
            ddd = match_ddd.group(1)
            return STATES_DDD.get(ddd, '')
        return ''

    @staticmethod
    def parse_price(price_str):
        """Converte string de preço (ex: 'R$ 2.400') para número."""
        if not price_str or "combinar" in price_str.lower():
            return None
        
        # Remove R$, espaços e pontos de milhar, troca vírgula por ponto
        limpo = price_str.replace("R$", "").replace(".", "").replace(" ", "").replace(",", ".")
        try:
            # Se for só inteiro (sem casas decimais na original ou depois do split)
            if float(limpo) == int(float(limpo)):
                return int(float(limpo))
            return float(limpo)
        except ValueError:
            return None

    @staticmethod
    def parse_date(date_str):
        """Converte unix timestamp (1781720296) para string ISO."""
        if not date_str:
            return ""
        try:
            # Testa se é só numero (unix timestamp)
            if str(date_str).isdigit():
                return datetime.fromtimestamp(int(date_str)).isoformat()
            return str(date_str)
        except Exception:
            return str(date_str)

    @staticmethod
    def parse_next_data(script_content):
        """Transforma a string JSON da OLX numa lista de dicionários de anúncios."""
        try:
            data = json.loads(script_content)
            ads = data.get('props', {}).get('pageProps', {}).get('ads', [])
            return ads if ads else []
        except:
            return []

    @staticmethod
    def parse_deep_details(html_text):
        """Analisa o HTML da página do anúncio profundo e extrai telefone, descrição e vendedor."""
        resultado = {
            'description': '',
            'author': '',
            'phone': '',
            'detail_status': 'sem_dados',
        }
        soup = BeautifulSoup(html_text, 'html.parser')

        # Tenta formato antigo
        initial_data_tag = soup.find('script', id='initial-data')
        if initial_data_tag and initial_data_tag.get('data-json'):
            try:
                data = json.loads(initial_data_tag['data-json'])
                ad = data.get('ad', {})
                resultado['description'] = OlxParser.safe_str(ad.get('body') or ad.get('description', ''))
                resultado['author'] = OlxParser.safe_str(ad.get('user', {}).get('name', ''))
                
                phone_data = ad.get('phone', {})
                resultado['phone'] = OlxParser.safe_str(phone_data.get('phone', '')) if isinstance(phone_data, dict) else OlxParser.safe_str(phone_data)
                
                resultado['detail_status'] = '200'
                return resultado
            except:
                pass

        # Tenta formato atual
        next_data_tag = soup.find('script', id='__NEXT_DATA__')
        if next_data_tag and next_data_tag.string:
            try:
                data = json.loads(next_data_tag.string)
                page_props = data.get('props', {}).get('pageProps', {})
                ad = page_props.get('ad', page_props.get('adDetail', {}))
                
                if ad:
                    resultado['description'] = OlxParser.safe_str(ad.get('body') or ad.get('description', ''))
                    resultado['author'] = OlxParser.safe_str(ad.get('user', {}).get('name', '') if isinstance(ad.get('user'), dict) else '')
                    
                    phone_data = ad.get('phone', {})
                    resultado['phone'] = OlxParser.safe_str(phone_data.get('phone', '')) if isinstance(phone_data, dict) else OlxParser.safe_str(phone_data)
                    
                    resultado['detail_status'] = '200'
                    return resultado
            except:
                pass

        return resultado

# ============================================================================
# CLASSE 2: STORAGE MANAGER (Persistência em Arquivos e Banco de Dados)
# ============================================================================
class StorageManager:
    def __init__(self, termo_busca, estado_nome, modo_profundo, job_id, db_client=None, redis_client=None):
        self.termo_busca = termo_busca
        self.estado_nome = estado_nome
        self.modo_profundo = modo_profundo
        self.job_id = job_id
        
        # Nomes de Arquivos
        self.nome_base_arquivo = f"resultados_{termo_busca.replace(' ', '_')}_{estado_nome}"
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
        self.mongo_client = db_client
        if self.mongo_client:
            db = self.mongo_client["olx_extractor"]
            self.anuncios_col = db["anuncios"]
            self.extractions_col = db["extractions"]
            self.price_history_col = db["price_history"]

        # Redis Config
        self.redis_client = redis_client

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
        if not hasattr(self, 'anuncios_col') or not self.anuncios_col or not list_id:
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
                print(f"Erro ao salvar no MongoDB: {e}")

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
                
                # Se for completado, não gravamos no redis mais para não sobrescrever a leitura do app.py que vai puxar do Mongo.
                if mapping["status"] == "completed":
                    return

                key = f"job:{self.job_id}"
                self.redis_client.hset(key, mapping=mapping)
                self.redis_client.expire(key, 86400) # Expira em 24h
            except Exception as e:
                print(f"Erro ao atualizar status no Redis: {e}")

    def close(self):
        self.f_csv.close()
        self.f_json.close()


# ============================================================================
# CLASSE 3: SCRAPER (Navegação, Sessão HTTP e Orquestração)
# ============================================================================
class OlxScraper:
    def __init__(self, termo_busca, estado, paginas_busca, modo_profundo, limite_detalhes, job_id=None, db_client=None, redis_client=None):
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

        self.storage = StorageManager(termo_busca, estado_nome, modo_profundo, job_id, db_client, redis_client)

    def check_cancel(self):
        """Verifica se o usuário requisitou o cancelamento deste job."""
        if self.storage.redis_client and self.job_id:
            try:
                if self.storage.redis_client.get(f"job:{self.job_id}:cancel"):
                    return True
            except:
                pass
        return False

    def scrape_deep_details(self, url, title):
        """Entra na página individual do anúncio para extrair telefone e descrição."""
        print(f"    [>>] Detalhe [{self.storage.detalhes_coletados + 1}] {title[:50]}...")
        
        prog_detalhe = 45 + int((self.storage.detalhes_coletados / self.limite_detalhes) * 45) if self.limite_detalhes else 60
        self.storage.update_job_status({
            "status": "details",
            "progress": min(prog_detalhe, 90),
            "message": f"Coletando detalhes do anúncio {self.storage.detalhes_coletados + 1}..."
        })
        
        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 403:
                return {'description': '', 'author': '', 'phone': '', 'detail_status': '403'}
            if response.status_code != 200:
                return {'description': '', 'author': '', 'phone': '', 'detail_status': str(response.status_code)}
            
            return OlxParser.parse_deep_details(response.text)
        except Exception as e:
            status = 'timeout' if 'timeout' in str(e).lower() else f'erro: {str(e)[:60]}'
            return {'description': '', 'author': '', 'phone': '', 'detail_status': status}

    def run(self):
        print("=" * 60)
        print("  ROBÔ OLX BRASIL - Extrator de Anúncios (OO Refatorado)")
        print("=" * 60)
        
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
                    print("  [X] Cancelamento detectado. Interrompendo paginação.")
                    is_cancelled = True
                    break

                url = self.url_base_template.format(pagina=i)
                print(f"[BUSCA] Raspando página {i} de {self.paginas_busca}...")
                
                progresso_atual = int((i / self.paginas_busca) * 30)
                self.storage.update_job_status({
                    "status": "scraping",
                    "progress": progresso_atual,
                    "message": f"Coletando anúncios básicos: página {i} de {self.paginas_busca}"
                })

                try:
                    response = self.session.get(url, timeout=15)
                    if response.status_code != 200:
                        print(f"  [!] Página {i} retornou status {response.status_code}")
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
                            'price': preco_num if preco_num is not None else 0, # Usado por front-end numérico
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
                    print(f"  [X] Erro na página {i}: {e}")
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
                    print("  [X] Cancelamento detectado antes dos detalhes.")
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
                        print(f"    [!] Buscando detalhes de {len(tarefas_profundas)} anúncios validados...")
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
                                # Sessão isolada para evitar crash entre threads
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

                        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
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
                # Caso modo_profundo seja false, anúncios_validos terão só dados básicos.
                for row in anuncios_validos:
                    self.storage.save_anuncio(row)

        finally:
            self.storage.close()

        # Status Final
        if is_cancelled:
            self.storage.update_job_status({"status": "cancelled", "progress": 100, "message": "Extração cancelada pelo usuário"})
        else:
            self.storage.update_job_status({"progress": 100, "message": "Gerando CSV, JSONL e persistindo no banco..."})
            
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


# ============================================================================
# WRAPPER PÚBLICO (Garante compatibilidade com app.py)
# ============================================================================
def scrape_olx(termo_busca, estado, paginas_busca=5, modo_profundo=True, limite_detalhes=20, job_id=None, db=None, redis_conn=None):
    """
    Função principal wrapper. Mantém compatibilidade com o app.py.
    """
    # Se não passaram db explicitly, tentamos conectar aqui (como antes)
    if db is None:
        try:
            mongo_client = MongoClient("mongodb://127.0.0.1:27017/", serverSelectionTimeoutMS=2000)
            mongo_client.admin.command("ping")
        except:
            mongo_client = None
    else:
        mongo_client = db

    scraper = OlxScraper(
        termo_busca=termo_busca,
        estado=estado,
        paginas_busca=paginas_busca,
        modo_profundo=modo_profundo,
        limite_detalhes=limite_detalhes,
        job_id=job_id,
        db_client=mongo_client,
        redis_client=redis_conn
    )
    
    return scraper.run()


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  BEM-VINDO AO ROBÔ OLX!")
    print("=" * 60)
    
    termo = input("O que você deseja buscar? (ex: celular, notebook, carro): ").strip()
    if not termo:
        termo = "imoveis"
        
    estado_input = input("Em qual estado? (ex: pa, sp, rj) ou aperte Enter para o Brasil todo: ").strip()
    if not estado_input:
        estado_input = "brasil"
        
    print("\nIniciando...")
    csv_resultado = scrape_olx(termo, estado_input, paginas_busca=1, modo_profundo=False, limite_detalhes=5)
    print(f"\nExtração concluída com sucesso! Arquivo gerado: {csv_resultado}")
