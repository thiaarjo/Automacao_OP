from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import csv
import time
import random

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

    if not ads:
        return []

    return ads

def scrape_olx():
    print("Iniciando o robô ultra-rápido da OLX com curl_cffi...")

    arquivo_csv = 'resultados_olx_pa.csv'
    arquivo_json = 'resultados_olx_pa.jsonlines'

    campos = [
        'title',
        'price',
        'location',
        'url',
        'list_id',
        'date'
    ]

    total_anuncios = 0
    ids_vistos = set()
    paginas_vazias = 0

    # Inicializa uma sessão que forja perfeitamente a assinatura TLS de um Chrome atualizado
    session = requests.Session(impersonate="chrome120")

    with open(arquivo_csv, 'w', newline='', encoding='utf-8-sig') as f_csv, \
         open(arquivo_json, 'w', encoding='utf-8') as f_json:

        writer = csv.DictWriter(f_csv, fieldnames=campos)
        writer.writeheader()

        for i in range(1, 101):
            url = f'https://www.olx.com.br/estado-pa?q=imoveis&o={i}'

            print(f"Raspando página {i} de 100...")

            try:
                # O timeout impede que o script trave se a OLX demorar pra responder
                response = session.get(url, timeout=15)

                if response.status_code != 200:
                    print(f"Aviso: página {i} retornou status {response.status_code}")
                    if response.status_code == 403:
                        print("Fomos bloqueados nesta página! Pulando para a próxima...")
                    time.sleep(random.uniform(2, 4))
                    continue

                # Usa o BeautifulSoup para ler o HTML puro instantaneamente
                soup = BeautifulSoup(response.text, 'html.parser')
                script_tag = soup.find('script', id='__NEXT_DATA__')

                if not script_tag or not script_tag.string:
                    print(f"Aviso: página {i} sem __NEXT_DATA__.")
                    paginas_vazias += 1
                    continue

                script_content = script_tag.string
                anuncios = extrair_anuncios_do_next_data(script_content)

                if not anuncios:
                    print(f"Aviso: nenhum anúncio encontrado na página {i}.")
                    paginas_vazias += 1

                    if paginas_vazias >= 3:
                        print("Três páginas seguidas sem anúncios. Encerrando.")
                        break

                    continue

                paginas_vazias = 0

                for anuncio in anuncios:
                    if not anuncio:
                        continue

                    titulo = anuncio.get('subject') or anuncio.get('title')

                    if not titulo:
                        continue

                    list_id = safe_str(
                        anuncio.get('listId') or anuncio.get('list_id')
                    )

                    if list_id and list_id in ids_vistos:
                        continue

                    if list_id:
                        ids_vistos.add(list_id)

                    row = {
                        'title': safe_str(titulo),
                        'price': safe_str(anuncio.get('price')),
                        'location': safe_str(anuncio.get('location')),
                        'url': safe_str(anuncio.get('url')),
                        'list_id': list_id,
                        'date': safe_str(anuncio.get('date'))
                    }

                    writer.writerow(row)
                    f_json.write(json.dumps(row, ensure_ascii=False) + '\n')

                    total_anuncios += 1

                # Pausa bem menor, pois o script agora é muito rápido!
                time.sleep(random.uniform(0.5, 1.5))

            except Exception as e:
                print(f"Aviso: não foi possível ler os dados da página {i}. Erro: {e}")
                time.sleep(random.uniform(2, 5))

    print("\nScraping finalizado!")
    print(f"Total de anúncios salvos: {total_anuncios}")
    print(f"Arquivo CSV: {arquivo_csv}")
    print(f"Arquivo JSONLines: {arquivo_json}")


if __name__ == '__main__':
    scrape_olx()
