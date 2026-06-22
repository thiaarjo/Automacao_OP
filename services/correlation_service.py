"""
Motor de Correlação e Consolidação de Dados (Sem IA)
====================================================
Responsabilidade: localizar, filtrar, agrupar e consolidar registros
do banco de dados usando apenas regras de código. A IA só será acionada
DEPOIS, recebendo o dossiê pronto.
"""

import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime


# ============================================================================
# TERMOS NEGATIVOS PADRÃO (sempre aplicados)
# ============================================================================
TERMOS_NEGATIVOS_PADRAO = [
    "capa", "capinha", "película", "pelicula", "carregador",
    "caixa", "sucata", "peça", "peca", "tela trincada",
    "tela quebrada", "defeito", "bloqueado", "retirada de peça",
    "retirada de peca", "carcaça", "carcaca", "bateria",
    "display", "touch", "placa", "cabo", "fonte",
    "suporte", "case", "bumper", "adesivo", "skin",
]


# ============================================================================
# 1. BUSCA PRINCIPAL NO BANCO
# ============================================================================
def buscar_anuncios_correlacionados(anuncios_col, termo: str, estado: str = None):
    """
    Busca todos os anúncios do banco que correspondem ao termo pesquisado.
    Usa índice textual ($text) com fallback para regex.
    """
    # Tenta busca textual primeiro (mais rápido com índice)
    filtro = {"$text": {"$search": termo}}

    if estado:
        filtro["state"] = estado.lower()

    try:
        docs = list(anuncios_col.find(filtro, {"_id": 0, "score": {"$meta": "textScore"}})
                     .sort([("score", {"$meta": "textScore"})])
                     .limit(5000))
        if docs:
            return docs
    except Exception:
        pass  # Índice textual pode não existir ainda, cai no fallback

    # Fallback: regex (mais lento, mas funciona sem índice)
    palavras = termo.strip().split()
    regex_pattern = ".*".join([re.escape(p) for p in palavras])
    filtro_regex = {"title": {"$regex": regex_pattern, "$options": "i"}}

    if estado:
        filtro_regex["state"] = estado.lower()

    docs = list(anuncios_col.find(filtro_regex, {"_id": 0}).limit(5000))
    return docs


# ============================================================================
# 2. APLICAR TERMOS NEGATIVOS (limpeza de falsos positivos)
# ============================================================================
def aplicar_termos_negativos(anuncios: list, termos_extras: list = None) -> tuple:
    """
    Remove anúncios que contenham termos negativos no título ou descrição.
    Retorna (anuncios_limpos, anuncios_removidos_por_termo).
    """
    todos_termos = TERMOS_NEGATIVOS_PADRAO.copy()
    if termos_extras:
        todos_termos.extend([t.strip().lower() for t in termos_extras if t.strip()])

    # Compila um regex com todos os termos negativos para performance
    if not todos_termos:
        return anuncios, []

    pattern = re.compile(
        "|".join([re.escape(t) for t in todos_termos]),
        re.IGNORECASE
    )

    limpos = []
    removidos = []
    for a in anuncios:
        texto = f"{a.get('title', '')} {a.get('descricao', '') or a.get('description', '')}".lower()
        if pattern.search(texto):
            removidos.append(a)
        else:
            limpos.append(a)

    return limpos, removidos


# ============================================================================
# 3. FILTRAR POR FAIXA DE PREÇO E REMOVER OUTLIERS
# ============================================================================
def remover_outliers(anuncios: list, preco_min: float = None, preco_max: float = None) -> tuple:
    """
    1. Remove preços nulos/zero.
    2. Aplica filtro manual de preco_min/preco_max se informado.
    3. Calcula mediana e remove outliers usando regra baseada na mediana.
    Retorna (anuncios_validos, outliers_removidos).
    """
    # Fase 1: remove inválidos
    validos = []
    outliers = []

    for a in anuncios:
        preco = a.get("price")
        if preco is None or preco == 0:
            outliers.append({**a, "motivoRemocao": "preço nulo ou zero"})
            continue
        if not isinstance(preco, (int, float)):
            try:
                preco = float(preco)
                a["price"] = preco
            except (ValueError, TypeError):
                outliers.append({**a, "motivoRemocao": "preço não numérico"})
                continue

        # Fase 2: filtro manual
        if preco_min is not None and preco < preco_min:
            outliers.append({**a, "motivoRemocao": f"abaixo do mínimo ({preco_min})"})
            continue
        if preco_max is not None and preco > preco_max:
            outliers.append({**a, "motivoRemocao": f"acima do máximo ({preco_max})"})
            continue

        validos.append(a)

    if len(validos) < 3:
        return validos, outliers

    # Fase 3: remoção estatística por mediana (40% a 250%)
    precos = [a["price"] for a in validos]
    mediana = statistics.median(precos)

    if mediana > 0:
        limite_inferior = mediana * 0.40
        limite_superior = mediana * 2.50
        finais = []
        for a in validos:
            if a["price"] < limite_inferior:
                outliers.append({**a, "motivoRemocao": f"abaixo de 40% da mediana ({mediana:.0f})"})
            elif a["price"] > limite_superior:
                outliers.append({**a, "motivoRemocao": f"acima de 250% da mediana ({mediana:.0f})"})
            else:
                finais.append(a)
        return finais, outliers

    return validos, outliers


# ============================================================================
# 4. CALCULAR ESTATÍSTICAS
# ============================================================================
def calcular_estatisticas(anuncios: list) -> dict:
    """Calcula média, mediana, mínimo, máximo e desvio padrão dos preços."""
    if not anuncios:
        return {
            "totalAnunciosCorrelacionados": 0,
            "precoMedio": 0, "precoMediana": 0,
            "menorPreco": 0, "maiorPreco": 0,
            "desvioPadrao": 0
        }

    precos = [a["price"] for a in anuncios if a.get("price")]

    if not precos:
        return {
            "totalAnunciosCorrelacionados": len(anuncios),
            "precoMedio": 0, "precoMediana": 0,
            "menorPreco": 0, "maiorPreco": 0,
            "desvioPadrao": 0
        }

    return {
        "totalAnunciosCorrelacionados": len(anuncios),
        "precoMedio": round(statistics.mean(precos), 2),
        "precoMediana": round(statistics.median(precos), 2),
        "menorPreco": min(precos),
        "maiorPreco": max(precos),
        "desvioPadrao": round(statistics.stdev(precos), 2) if len(precos) > 1 else 0
    }


# ============================================================================
# 5. AGRUPAR VENDEDORES
# ============================================================================
def agrupar_vendedores(anuncios: list) -> list:
    """
    Agrupa anúncios por vendedor (prioridade: telefone > vendedor > vendedor+cidade).
    Classifica: 1 = comum, 2-4 = recorrente, 5+ = possível lojista.
    """
    grupos = defaultdict(list)

    for a in anuncios:
        telefone = a.get("telefone") or a.get("phone") or ""
        vendedor = a.get("vendedor") or a.get("author") or ""
        cidade = a.get("city", "")

        # Prioridade de chave de agrupamento
        if telefone.strip():
            chave = f"tel:{telefone.strip()}"
        elif vendedor.strip():
            chave = f"vend:{vendedor.strip().lower()}|{cidade.strip().lower()}"
        else:
            continue  # sem identificação

        grupos[chave].append(a)

    resultado = []
    for chave, ads in grupos.items():
        qtd = len(ads)
        if qtd < 2:
            continue  # vendedor comum, não interessa

        primeiro = ads[0]
        classificacao = "vendedor recorrente" if qtd < 5 else "possível lojista/revendedor"

        resultado.append({
            "telefone": primeiro.get("telefone") or primeiro.get("phone") or "",
            "vendedor": primeiro.get("vendedor") or primeiro.get("author") or "",
            "cidade": primeiro.get("city", ""),
            "quantidadeAnuncios": qtd,
            "classificacao": classificacao,
            "anuncioIds": [a.get("listId") or a.get("list_id", "") for a in ads],
        })

    resultado.sort(key=lambda x: x["quantidadeAnuncios"], reverse=True)
    return resultado


# ============================================================================
# 6. ANEXAR HISTÓRICO DE PREÇOS
# ============================================================================
def anexar_historico_precos(anuncios: list, price_history_col) -> list:
    """
    Cruza cada anúncio com a coleção price_history para montar
    o mini-relatório de variação de preço.
    """
    if not price_history_col:
        return []

    list_ids = [a.get("listId") or a.get("list_id") for a in anuncios if a.get("listId") or a.get("list_id")]
    if not list_ids:
        return []

    # Busca todo o histórico relevante de uma vez só (batch)
    historico_cursor = price_history_col.find(
        {"list_id": {"$in": list_ids}},
        {"_id": 0, "list_id": 1, "price": 1, "observedAt": 1}
    ).sort("observedAt", 1)

    # Agrupa por list_id
    por_id = defaultdict(list)
    for h in historico_cursor:
        por_id[h["list_id"]].append(h)

    resultado = []
    for lid, registros in por_id.items():
        precos = [r["price"] for r in registros if r.get("price")]
        if len(precos) < 2:
            continue

        preco_atual = precos[-1]
        menor = min(precos)
        maior = max(precos)
        variacao = round(((preco_atual - precos[0]) / precos[0]) * 100, 2) if precos[0] else 0

        resultado.append({
            "listId": lid,
            "precoAtual": preco_atual,
            "menorPrecoHistorico": menor,
            "maiorPrecoHistorico": maior,
            "variacaoPercentual": variacao,
            "quantidadeObservacoes": len(precos)
        })

    return resultado


# ============================================================================
# 7. INSIGHTS TÉCNICOS (regras de código, NÃO IA)
# ============================================================================
def gerar_insights_tecnicos(estatisticas: dict, vendedores: list, historico: list, outliers: list) -> list:
    """
    Gera observações automáticas baseadas em regras de código.
    Nenhuma IA é usada aqui — são regras if/else determinísticas.
    """
    insights = []

    total = estatisticas.get("totalAnunciosCorrelacionados", 0)
    if total == 0:
        insights.append({"tipo": "risco", "mensagem": "Nenhum anúncio encontrado para este termo após filtragem.", "severidade": "alta"})
        return insights

    # Insight 1: Concentração de mercado
    lojistas = [v for v in vendedores if v["classificacao"] == "possível lojista/revendedor"]
    if lojistas:
        total_lojistas_ads = sum(v["quantidadeAnuncios"] for v in lojistas)
        pct = round((total_lojistas_ads / total) * 100, 1)
        insights.append({
            "tipo": "mercado",
            "mensagem": f"{len(lojistas)} possível(is) lojista(s) detectado(s), responsáveis por {total_lojistas_ads} anúncios ({pct}% do total).",
            "severidade": "media"
        })

    # Insight 2: Dispersão de preço
    desvio = estatisticas.get("desvioPadrao", 0)
    media = estatisticas.get("precoMedio", 1)
    if media > 0:
        cv = (desvio / media) * 100
        if cv > 30:
            insights.append({
                "tipo": "preco",
                "mensagem": f"Alta dispersão de preços detectada (CV={cv:.1f}%). Pode indicar variações de estado de conservação ou modelos diferentes.",
                "severidade": "media"
            })
        elif cv < 10:
            insights.append({
                "tipo": "oportunidade",
                "mensagem": f"Preços muito uniformes (CV={cv:.1f}%). Mercado estável para este item.",
                "severidade": "baixa"
            })

    # Insight 3: Tendência de preço
    tendencias_queda = [h for h in historico if h["variacaoPercentual"] < -5]
    tendencias_alta = [h for h in historico if h["variacaoPercentual"] > 5]
    if len(tendencias_queda) > len(tendencias_alta) and tendencias_queda:
        insights.append({
            "tipo": "tendencia",
            "mensagem": f"Tendência de queda: {len(tendencias_queda)} anúncios tiveram redução de preço > 5%.",
            "severidade": "baixa"
        })
    elif len(tendencias_alta) > len(tendencias_queda) and tendencias_alta:
        insights.append({
            "tipo": "tendencia",
            "mensagem": f"Tendência de alta: {len(tendencias_alta)} anúncios tiveram aumento de preço > 5%.",
            "severidade": "media"
        })

    # Insight 4: Outliers removidos
    if outliers:
        insights.append({
            "tipo": "limpeza",
            "mensagem": f"{len(outliers)} anúncio(s) removido(s) como outlier(s) ou falso(s) positivo(s).",
            "severidade": "baixa"
        })

    return insights


# ============================================================================
# 8. ORQUESTRADOR: GERA O DOSSIÊ COMPLETO
# ============================================================================
def gerar_dossie_correlacionado(
    anuncios_col,
    price_history_col,
    termo: str,
    estado: str = None,
    preco_min: float = None,
    preco_max: float = None,
    excluir_termos_extras: list = None
) -> dict:
    """
    Função principal que orquestra todo o pipeline de correlação.
    Retorna o dossiê JSON consolidado pronto para consumo
    (pelo frontend, por relatórios ou, futuramente, pela IA).
    """
    # 1. Busca bruta
    anuncios_brutos = buscar_anuncios_correlacionados(anuncios_col, termo, estado)

    # 2. Limpeza por termos negativos
    anuncios_limpos, removidos_termo = aplicar_termos_negativos(anuncios_brutos, excluir_termos_extras)

    # 3. Remoção de outliers
    anuncios_validos, outliers = remover_outliers(anuncios_limpos, preco_min, preco_max)

    # Junta todos os removidos para transparência
    todos_outliers = []
    for r in removidos_termo:
        todos_outliers.append({
            "title": r.get("title", ""), 
            "price": r.get("price", 0), 
            "motivo": "termo negativo detectado",
            "city": r.get("city", ""),
            "state": r.get("state", "")
        })
    for o in outliers:
        todos_outliers.append({
            "title": o.get("title", ""), 
            "price": o.get("price", 0), 
            "motivo": o.get("motivoRemocao", "outlier"),
            "city": o.get("city", ""),
            "state": o.get("state", "")
        })

    # 4. Estatísticas
    stats = calcular_estatisticas(anuncios_validos)

    # 5. Vendedores
    vendedores = agrupar_vendedores(anuncios_validos)

    # 6. Histórico de preços
    historico = anexar_historico_precos(anuncios_validos, price_history_col)

    # 7. Insights técnicos (código, não IA)
    insights = gerar_insights_tecnicos(stats, vendedores, historico, todos_outliers)

    # Monta lista de termos negativos aplicados
    termos_aplicados = TERMOS_NEGATIVOS_PADRAO.copy()
    if excluir_termos_extras:
        termos_aplicados.extend([t.strip().lower() for t in excluir_termos_extras if t.strip()])

    # 8. Dossiê Final
    return {
        "termoPesquisado": termo,
        "geradoEm": datetime.now().isoformat(),
        "filtrosAplicados": {
            "estado": estado,
            "precoMin": preco_min,
            "precoMax": preco_max,
            "termosNegativos": termos_aplicados
        },
        "estatisticasGlobais": stats,
        "anunciosValidados": anuncios_validos,
        "vendedoresFrequentes": vendedores,
        "historicoVariacaoPrecos": historico,
        "outliersRemovidos": todos_outliers,
        "insightsTecnicos": insights
    }
