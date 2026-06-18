from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import csv
import time
import random
import re
import sys
import urllib.parse
from datetime import datetime
from pymongo import MongoClient

# ============================================================================
# CONFIGURAÇÃO DO MONGODB
# ============================================================================
# Conecta ao MongoDB local
try:
    mongo_client = MongoClient("mongodb://127.0.0.1:27017/", serverSelectionTimeoutMS=2000)
    db = mongo_client["olx_extractor"]
    anuncios_col = db["anuncios"]
    extractions_col = db["extractions"]
except Exception as e:
    print(f"Erro ao conectar no MongoDB: {e}")
    mongo_client = None

# ============================================================================
# DICIONÁRIO DDD → ESTADO (adaptado do pyolxbrazil)
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
# FUNÇÕES AUXILIARES
# ============================================================================

def safe_str(value):
    """
    Garante que qualquer valor seja salvo como texto.
    Evita erro quando location, price ou outro campo vier como dict, list ou None.
    """
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def extrair_location_legivel(location_raw):
    """
    O campo 'location' da OLX às vezes vem como string e às vezes como dict.
    Essa função normaliza ambos os formatos para uma string legível.
    Retorna (location_str, cidade, bairro).
    """
    if location_raw is None:
        return '', '', ''

    if isinstance(location_raw, str):
        # Já é string, ex: 'Belém, Marco - DDD 91'
        partes = location_raw.split(',')
        cidade = partes[0].strip() if partes else ''
        bairro = ''
        if len(partes) > 1:
            bairro_parte = partes[1].split(' - ')[0].strip()
            bairro = bairro_parte
        return location_raw, cidade, bairro

    if isinstance(location_raw, dict):
        # Dict com campos como municipality, neighbourhood, uf, ddd
        cidade = location_raw.get('municipality', '')
        bairro = location_raw.get('neighbourhood', '')
        uf = location_raw.get('uf', '')
        ddd = location_raw.get('ddd', '')
        region = location_raw.get('region', '')

        # Monta string legível
        partes = []
        if cidade:
            partes.append(cidade)
        if bairro:
            partes.append(bairro)
        location_str = ', '.join(partes)
        if ddd:
            location_str += f' - DDD {ddd}'
        elif uf:
            location_str += f' - {uf}'

        return location_str, cidade, bairro

    return safe_str(location_raw), '', ''


def extrair_estado_do_ddd(location_str):
    """
    Recebe uma string como 'Belém, Marco - DDD 91'
    e retorna o nome do estado ('Pará').
    """
    match = re.search(r'DDD\s*(\d{2})', location_str)
    if match:
        ddd = match.group(1)
        return STATES_DDD.get(ddd, '')
    return ''


def extrair_anuncios_do_next_data(script_content):
    """
    Recebe o conteúdo do __NEXT_DATA__ e tenta extrair a lista de anúncios.
    """
    data = json.loads(script_content)
    ads = (
        data
        .get('props', {})
        .get('pageProps', {})
        .get('ads', [])
    )
    return ads if ads else []


def extrair_detalhes_anuncio(session, url):
    """
    Entra na página individual de um anúncio e extrai detalhes completos:
    descrição, nome do anunciante e telefone.

    Retorna um dicionário com os campos extras e o status da requisição.
    """
    resultado = {
        'description': '',
        'author': '',
        'phone': '',
        'detail_status': 'sem_dados',
    }

    try:
        response = session.get(url, timeout=15)

        if response.status_code == 403:
            resultado['detail_status'] = '403'
            return resultado

        if response.status_code != 200:
            resultado['detail_status'] = str(response.status_code)
            return resultado

        soup = BeautifulSoup(response.text, 'html.parser')

        # Tenta encontrar o JSON na tag 'initial-data' (formato antigo do pyolxbrazil)
        initial_data_tag = soup.find('script', id='initial-data')
        if initial_data_tag and initial_data_tag.get('data-json'):
            data = json.loads(initial_data_tag['data-json'])
            ad = data.get('ad', {})

            resultado['description'] = safe_str(ad.get('body') or ad.get('description', ''))
            resultado['author'] = safe_str(ad.get('user', {}).get('name', ''))

            phone_data = ad.get('phone', {})
            if isinstance(phone_data, dict):
                resultado['phone'] = safe_str(phone_data.get('phone', ''))
            else:
                resultado['phone'] = safe_str(phone_data)

            resultado['detail_status'] = '200'
            return resultado

        # Tenta encontrar o JSON na tag '__NEXT_DATA__' (formato atual)
        next_data_tag = soup.find('script', id='__NEXT_DATA__')
        if next_data_tag and next_data_tag.string:
            data = json.loads(next_data_tag.string)
            page_props = data.get('props', {}).get('pageProps', {})

            # A estrutura pode variar: às vezes os dados ficam em 'ad', às vezes em 'adDetail'
            ad = page_props.get('ad', page_props.get('adDetail', {}))

            if ad:
                resultado['description'] = safe_str(
                    ad.get('body') or ad.get('description', '')
                )
                resultado['author'] = safe_str(
                    ad.get('user', {}).get('name', '')
                    if isinstance(ad.get('user'), dict) else ''
                )

                phone_data = ad.get('phone', {})
                if isinstance(phone_data, dict):
                    resultado['phone'] = safe_str(phone_data.get('phone', ''))
                else:
                    resultado['phone'] = safe_str(phone_data)

                resultado['detail_status'] = '200'
                return resultado

        resultado['detail_status'] = 'sem_dados'
        return resultado

    except Exception as e:
        if 'timeout' in str(e).lower() or 'timed out' in str(e).lower():
            resultado['detail_status'] = 'timeout'
        else:
            resultado['detail_status'] = f'erro: {str(e)[:60]}'
        return resultado


# ============================================================================
# FUNÇÃO PRINCIPAL
# ============================================================================

def scrape_olx(termo_busca, estado, paginas_busca=5, modo_profundo=True, limite_detalhes=20, job_id=None):
    print("=" * 60)
    print("  ROBÔ OLX BRASIL - Extrator de Anúncios")
    print("=" * 60)
    print(f"  Buscando por : {termo_busca.upper()}")
    print(f"  Estado       : {estado.upper()}")
    print(f"  Modo         : {'PROFUNDO (detalhes de cada anúncio)' if modo_profundo else 'RÁPIDO (apenas lista)'}")
    if modo_profundo and limite_detalhes:
        print(f"  Limite prof. : {limite_detalhes} anúncios")
    print(f"  Páginas máx. : {paginas_busca}")
    print("=" * 60)
    print()

    # Função auxiliar para atualizar o MongoDB com o status do Job
    def update_job_status(status_info):
        if job_id and mongo_client:
            try:
                extractions_col.update_one(
                    {"job_id": job_id},
                    {"$set": status_info},
                    upsert=True
                )
            except Exception as e:
                print(f"Erro ao atualizar status do job: {e}")

    # Formata a URL de acordo com a pesquisa
    termo_url = urllib.parse.quote(termo_busca)
    estado_url = estado.lower()
    
    if estado_url in ['brasil', 'br', '']:
        url_base_template = f'https://www.olx.com.br/brasil?q={termo_url}&o={{pagina}}'
        estado_nome = 'brasil'
    else:
        url_base_template = f'https://www.olx.com.br/estado-{estado_url}?q={termo_url}&o={{pagina}}'
        estado_nome = estado_url

    nome_base_arquivo = f"resultados_{termo_busca.replace(' ', '_')}_{estado_nome}"
    arquivo_csv = f'{nome_base_arquivo}.csv'
    arquivo_json = f'{nome_base_arquivo}.jsonlines'

    # Campos do CSV: os básicos + estado + campos profundos (se ativado)
    campos = [
        'title',
        'price',
        'location',
        'city',
        'neighborhood',
        'state',
        'url',
        'list_id',
        'date',
    ]

    if modo_profundo:
        campos += ['description', 'author', 'phone', 'detail_status']

    total_anuncios = 0
    total_duplicados = 0
    detalhes_coletados = 0
    ids_vistos = set()
    titulos_vistos = set()  # Segunda camada de deduplicação: título + preço
    paginas_vazias = 0

    # Sessão com fingerprint TLS de Chrome real para burlar o DataDome
    session = requests.Session(impersonate="chrome120")

    # Abre os arquivos com buffering de linha para salvar incrementalmente
    f_csv = open(arquivo_csv, 'w', newline='', encoding='utf-8-sig')
    f_json = open(arquivo_json, 'w', encoding='utf-8')

    try:
        writer = csv.DictWriter(f_csv, fieldnames=campos)
        writer.writeheader()
        f_csv.flush()

        # ---- FASE 1: Raspar as páginas de busca ----
        for i in range(1, paginas_busca + 1):
            url = url_base_template.format(pagina=i)
            print(f"[BUSCA] Raspando página {i} de {paginas_busca}...")
            
            # Atualiza progresso (Scraping = até 45%)
            progresso_atual = int((i / paginas_busca) * 45)
            update_job_status({
                "status": "scraping",
                "progress": progresso_atual,
                "message": f"Raspando página {i} de {paginas_busca} da OLX...",
                "totalAnuncios": total_anuncios,
                "detalhesColetados": detalhes_coletados,
                "duplicadosRemovidos": total_duplicados
            })

            try:
                response = session.get(url, timeout=15)

                if response.status_code != 200:
                    print(f"  [!] Página {i} retornou status {response.status_code}")
                    if response.status_code == 403:
                        print("  [!] Bloqueio detectado! Fazendo pausa longa...")
                        time.sleep(random.uniform(5, 10))
                    continue

                soup = BeautifulSoup(response.text, 'html.parser')
                script_tag = soup.find('script', id='__NEXT_DATA__')

                if not script_tag or not script_tag.string:
                    print(f"  [!] Página {i} sem __NEXT_DATA__.")
                    paginas_vazias += 1
                    continue

                anuncios = extrair_anuncios_do_next_data(script_tag.string)

                if not anuncios:
                    print(f"  [!] Nenhum anúncio encontrado na página {i}.")
                    paginas_vazias += 1
                    if paginas_vazias >= 3:
                        print("  [STOP] Três páginas seguidas sem anúncios. Encerrando busca.")
                        break
                    continue

                paginas_vazias = 0

                for anuncio in anuncios:
                    if not anuncio:
                        continue

                    titulo = anuncio.get('subject') or anuncio.get('title')
                    if not titulo:
                        continue

                    list_id = safe_str(anuncio.get('listId') or anuncio.get('list_id'))

                    # --- DEDUPLICAÇÃO CAMADA 1: por list_id ---
                    if list_id and list_id in ids_vistos:
                        total_duplicados += 1
                        continue
                    if list_id:
                        ids_vistos.add(list_id)

                    # --- DEDUPLICAÇÃO CAMADA 2: por título + preço ---
                    preco_str = safe_str(anuncio.get('price'))
                    chave_titulo = f"{titulo.strip().lower()}|{preco_str.strip().lower()}"
                    if chave_titulo in titulos_vistos:
                        total_duplicados += 1
                        continue
                    titulos_vistos.add(chave_titulo)

                    # --- NORMALIZA LOCATION ---
                    location_raw = anuncio.get('location')
                    location_str, cidade, bairro = extrair_location_legivel(location_raw)
                    estado = extrair_estado_do_ddd(location_str)

                    row = {
                        'title': safe_str(titulo),
                        'price': preco_str,
                        'location': location_str,
                        'city': cidade,
                        'neighborhood': bairro,
                        'state': estado,
                        'url': safe_str(anuncio.get('url')),
                        'list_id': list_id,
                        'date': safe_str(anuncio.get('date')),
                    }

                    # Se modo profundo está ligado, busca detalhes agora
                    if modo_profundo:
                        pode_buscar = (limite_detalhes is None or detalhes_coletados < limite_detalhes)

                        if pode_buscar and row['url']:
                            print(f"    [>>] Detalhe [{detalhes_coletados + 1}] {row['title'][:50]}...")
                            
                            # Atualiza status visual para os detalhes (progresso 45% -> 90%)
                            prog_detalhe = 45 + int((detalhes_coletados / limite_detalhes) * 45) if limite_detalhes else 60
                            update_job_status({
                                "status": "details",
                                "progress": min(prog_detalhe, 90),
                                "message": f"Coletando detalhes do anúncio {detalhes_coletados + 1}...",
                                "totalAnuncios": total_anuncios,
                                "detalhesColetados": detalhes_coletados,
                                "duplicadosRemovidos": total_duplicados
                            })
                            
                            detalhes = extrair_detalhes_anuncio(session, row['url'])
                            row.update(detalhes)
                            detalhes_coletados += 1

                            # Pausa maior no modo profundo para não ser bloqueado
                            time.sleep(random.uniform(2, 5))
                        else:
                            row['description'] = ''
                            row['author'] = ''
                            row['phone'] = ''
                            row['detail_status'] = 'pulado' if not pode_buscar else 'sem_url'

                    # Salva incrementalmente no CSV e JSONL
                    writer.writerow(row)
                    f_csv.flush()
                    f_json.write(json.dumps(row, ensure_ascii=False) + '\n')
                    f_json.flush()

                    # Salva no MongoDB (Faz upsert para não duplicar caso o list_id já exista)
                    if mongo_client:
                        try:
                            # Prepara o doc, adiciona timestamp de captura e job_id
                            doc = row.copy()
                            doc["_captured_at"] = datetime.now()
                            if job_id:
                                doc["job_id"] = job_id
                            anuncios_col.update_one(
                                {"list_id": list_id},
                                {"$set": doc},
                                upsert=True
                            )
                        except Exception as e:
                            print(f"Erro ao salvar no MongoDB: {e}")

                    total_anuncios += 1

                # Pausa entre páginas de busca
                time.sleep(random.uniform(0.5, 1.5))

            except Exception as e:
                print(f"  [X] Erro na página {i}: {e}")
                time.sleep(random.uniform(2, 5))

    finally:
        # Garante que os arquivos são fechados mesmo em caso de erro
        f_csv.close()
        f_json.close()

    # ---- RESUMO FINAL ----
    print()
    print("=" * 60)
    print("  SCRAPING FINALIZADO!")
    print("=" * 60)
    print(f"  Total de anúncios salvos: {total_anuncios}")
    print(f"  Duplicados removidos: {total_duplicados}")
    if modo_profundo:
        print(f"  Detalhes profundos coletados: {detalhes_coletados}")
    print(f"  Arquivo CSV: {arquivo_csv}")
    print(f"  Arquivo JSONLines: {arquivo_json}")
    print("=" * 60)
    # Atualiza o status final no MongoDB com os números totais
    update_job_status({
        "totalAnuncios": total_anuncios,
        "detalhesColetados": detalhes_coletados,
        "duplicadosRemovidos": total_duplicados
    })
    
    return arquivo_csv


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  BEM-VINDO AO ROBÔ OLX!")
    print("=" * 60)
    
    # Se rodar direto pelo terminal, usamos os valores padrão da CLI
    termo = input("O que você deseja buscar? (ex: celular, notebook, carro): ").strip()
    if not termo:
        termo = "imoveis"
        
    estado = input("Em qual estado? (ex: pa, sp, rj) ou aperte Enter para o Brasil todo: ").strip()
    if not estado:
        estado = "brasil"
        
    print("\nIniciando...")
    scrape_olx(termo, estado, paginas_busca=5, modo_profundo=True, limite_detalhes=20)
