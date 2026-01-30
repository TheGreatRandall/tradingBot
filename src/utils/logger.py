"""
Logging configuration.
"""
import sys
from loguru import logger


def setup_logger(
    log_level: str = "INFO",
    log_file: str = "logs/trading.log",
    rotation: str = "10 MB",
    retention: str = "30 days",
) -> None:
    """
    Configure logging for the application.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file
        rotation: When to rotate log files
        retention: How long to keep old logs
    """
    # Remove default handler
    logger.remove()

    # Console handler with color
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File handler
    logger.add(
        log_file,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation=rotation,
        retention=retention,
        compression="zip",
    )

    # Separate file for errors
    logger.add(
        log_file.replace(".log", "_errors.log"),
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation=rotation,
        retention=retention,
        compression="zip",
    )

    logger.info(f"Logger initialized with level={log_level}")
