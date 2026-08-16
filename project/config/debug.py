import debugpy

from config.custom_logger import setup_logger
from config.settings import Settings

settings = Settings()
logger = setup_logger(__name__, level=settings.log_level, fmt=settings.log_format)

debugpy.listen(("0.0.0.0", settings.debugpy_port))
logger.info("Waiting for debugger to attach on port %s", settings.debugpy_port)
debugpy.wait_for_client()
logger.info("Debugger attached")
