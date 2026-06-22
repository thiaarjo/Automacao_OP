import json
import re
from datetime import datetime
from bs4 import BeautifulSoup

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

class OlxParser:
    @staticmethod
    def safe_str(value):
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @staticmethod
    def extract_location(location_raw):
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
        if not price_str or "combinar" in price_str.lower():
            return None
        
        limpo = price_str.replace("R$", "").replace(".", "").replace(" ", "").replace(",", ".")
        try:
            if float(limpo) == int(float(limpo)):
                return int(float(limpo))
            return float(limpo)
        except ValueError:
            return None

    @staticmethod
    def parse_date(date_str):
        if not date_str:
            return ""
        try:
            if str(date_str).isdigit():
                return datetime.fromtimestamp(int(date_str)).isoformat()
            return str(date_str)
        except Exception:
            return str(date_str)

    @staticmethod
    def parse_next_data(script_content):
        try:
            data = json.loads(script_content)
            ads = data.get('props', {}).get('pageProps', {}).get('ads', [])
            return ads if ads else []
        except:
            return []

    @staticmethod
    def parse_deep_details(html_text):
        resultado = {
            'description': '',
            'author': '',
            'phone': '',
            'detail_status': 'sem_dados',
        }
        soup = BeautifulSoup(html_text, 'html.parser')

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
