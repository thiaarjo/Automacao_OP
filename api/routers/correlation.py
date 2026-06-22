from fastapi import APIRouter, HTTPException
from core.database import get_database
from services.correlation_service import gerar_dossie_correlacionado

router = APIRouter()

# =============================================================================
# MOTOR DE CORRELAÇÃO E CONSOLIDAÇÃO (Sem IA)
# =============================================================================
@router.get("/anuncios/correlacionados")
@router.get("/api/anuncios/correlacionados")
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
    db = get_database()
    if not db:
        raise HTTPException(status_code=500, detail="MongoDB não conectado")

    anuncios_col = db["anuncios"]
    price_history_col = db["price_history"]

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
