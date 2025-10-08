import os
import sys

import loguru

from api.constants import SERIALIZE_LOG_OUTPUT
from api.enums import Environment
from api.utils.worker import get_worker_id, is_worker_process
from pipecat.utils.context import run_id_var, turn_var

ENVIRONMENT = os.getenv("ENVIRONMENT", Environment.LOCAL.value)
ENABLE_TURN_LOGGING = os.getenv("ENABLE_TURN_LOGGING", "false").lower() == "true"

# We write different uvicorn forked worker log to a different
# file which is then synced to cloudwatch logs
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", None)

# Track if logging has been initialized
_logging_initialized = False


def inject_run_id(record):
    """Inject run_id from context variable into log record"""
    record["extra"]["run_id"] = run_id_var.get()

    # Only handle turn logging if enabled
    if ENABLE_TURN_LOGGING:
        # Get turn number with fallback mechanism
        turn = turn_var.get()

        # If turn is still 0, try the turn context manager
        if turn == 0:
            try:
                from api.services.pipecat.turn_context import get_turn_context_manager

                turn = get_turn_context_manager().get_turn()
            except ImportError:
                # Turn context manager not available
                pass

        record["extra"]["turn"] = turn
    else:
        # Turn logging disabled, use default value
        record["extra"]["turn"] = 0


def setup_logging():
    """Set up logging for the main application"""
    global _logging_initialized

    # Return early if already initialized
    if _logging_initialized:
        return

    log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()

    # Don't setup logging in test environment
    if ENVIRONMENT == Environment.TEST.value:
        return

    # Remove default loguru handler
    try:
        loguru.logger.remove(0)
    except ValueError:
        # Handler might already be removed
        pass

    # Patch loguru to inject run_id
    patched = loguru.logger.patch(inject_run_id)

    # Determine log format
    if ENABLE_TURN_LOGGING:
        log_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | <level>{level}</level> | [run_id={extra[run_id]}] [turn={extra[turn]}] | {file.name}:{line} | {message}"
    else:
        log_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | <level>{level}</level> | [run_id={extra[run_id]}] | {file.name}:{line} | {message}"

    # Add handler - either file or console
    if LOG_FILE_PATH:
        # Determine the actual log file path
        actual_log_path = LOG_FILE_PATH

        # If we're in a worker process, append worker ID to the filename
        if is_worker_process():
            worker_id = get_worker_id()
            # Split the path to insert worker ID before extension
            base_path, ext = os.path.splitext(LOG_FILE_PATH)
            actual_log_path = f"{base_path}-worker-{worker_id}{ext}"

        patched.add(
            actual_log_path,
            level=log_level,
            serialize=SERIALIZE_LOG_OUTPUT,  # Use JSON serialization for structured logs
            enqueue=True,  # Thread-safe writing
            backtrace=True,  # Include full traceback in exceptions
            diagnose=False,  # Don't include local variables in traceback for security
        )
    else:
        # Console handler (existing behavior)
        patched.add(
            sys.stdout,
            format=log_format,
            level=log_level,
            colorize=True,
        )

    loguru.logger = patched
    _logging_initialized = True
