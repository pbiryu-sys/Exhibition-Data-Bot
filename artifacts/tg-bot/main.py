#!/usr/bin/env python3
"""
Production entry-point for the Telegram bot.

Wraps bot.main() in an infinite restart loop: on any unexpected exception,
log it, wait 5 seconds, and start polling again. The bot never stops
permanently due to a runtime error.

Normal exits (KeyboardInterrupt, SystemExit) propagate so the process
manager can still shut down the bot cleanly.
"""
import logging
import os
import sys
import time

# Ensure imports resolve exactly as if running `python3 bot.py` from this dir
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("launcher")


def run_forever() -> None:
    import bot

    while True:
        try:
            logger.info("Starting bot polling…")
            bot.main()
            logger.warning("Polling loop exited cleanly — restarting in 5 s")
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — shutting down.")
            sys.exit(0)
        except SystemExit as exc:
            logger.info("SystemExit(%s) — shutting down.", exc.code)
            sys.exit(exc.code)
        except Exception:
            logger.exception("Bot crashed — restarting in 5 s")
        time.sleep(5)


if __name__ == "__main__":
    run_forever()
