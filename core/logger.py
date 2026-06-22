import logging
import os
import sys
from core.config import LOGS_DIR

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    
    # Previne que o logger adicione handlers duplicados se chamado múltiplas vezes
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Formato: 2026-06-19 10:30:00 | INFO | scraper.engine | Extração iniciada
        formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

        # Handler de Console
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # Handler de Arquivo
        file_handler = logging.FileHandler(os.path.join(LOGS_DIR, "app.log"), encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
    return logger
