"""仓库根目录兼容入口：python main.py（等价于 python -m exercise_extractor）。"""
import logging

from exercise_extractor.main import main


logging.basicConfig(
    filename="main.log",
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s:%(message)s",
    filemode="w",
)
logging.getLogger("toc_forge").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

if __name__ == "__main__":
    main()
