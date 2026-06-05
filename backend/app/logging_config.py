import logging
from logging.handlers import RotatingFileHandler

from app.config import Settings


LOGGER_NAME = 'entra_ig'


def configure_logging(settings: Settings) -> logging.Logger:
    settings.log_file_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(settings.log_level.upper())
    logger.handlers.clear()

    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        '%Y-%m-%dT%H:%M:%SZ',
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(settings.log_file_path, maxBytes=1_000_000, backupCount=5)
    file_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger
