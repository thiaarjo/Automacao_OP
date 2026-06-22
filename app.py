# ============================================================================
# ARQUIVO PONTE (COMPATIBILIDADE)
# Todo o código do servidor foi refatorado para o pacote `api/`.
# Mantenha este arquivo para não quebrar o comando `uvicorn app:app` antigo.
# ============================================================================

from api.app import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=5000, reload=True)
