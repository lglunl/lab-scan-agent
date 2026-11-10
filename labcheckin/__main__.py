import logging

import uvicorn

from labcheckin.config import load_config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    uvicorn.run("labcheckin.main:app", host=cfg.host, port=cfg.port, reload=False)


if __name__ == "__main__":
    main()
