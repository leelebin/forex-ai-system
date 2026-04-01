import logging
import subprocess
import sys
import time


def setup_watchdog_logger() -> logging.Logger:
    logger = logging.getLogger("watchdog")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler("watchdog.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)
    return logger


def main() -> None:
    logger = setup_watchdog_logger()
    restart_count = 0

    while True:
        logger.info("Starting main.py")
        process = subprocess.Popen([sys.executable, "main.py"])
        exit_code = process.wait()

        restart_count += 1
        logger.error(
            "main.py exited with code %s. Restart count=%s. Restarting in 5 seconds...",
            exit_code,
            restart_count,
        )
        time.sleep(5)


if __name__ == "__main__":
    main()
