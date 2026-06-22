# ============================================================================
# ARQUIVO PONTE (COMPATIBILIDADE)
# Todo o código de extração foi refatorado para o pacote `scraper/`.
# Mantenha este arquivo para não quebrar imports em códigos antigos.
# ============================================================================

from scraper.engine import scrape_olx

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  BEM-VINDO AO ROBÔ OLX! (MODO CLI)")
    print("=" * 60)
    
    termo = input("O que você deseja buscar? (ex: celular, notebook, carro): ").strip()
    if not termo:
        termo = "imoveis"
        
    estado_input = input("Em qual estado? (ex: pa, sp, rj) ou aperte Enter para o Brasil todo: ").strip()
    if not estado_input:
        estado_input = "brasil"
        
    print("\nIniciando...")
    resultado = scrape_olx(termo, estado_input, paginas_busca=1, modo_profundo=False, limite_detalhes=5)
    print(f"\nExtração concluída com sucesso! Arquivo gerado: {resultado.get('csv_file', 'Desconhecido')}")
