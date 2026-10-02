"""下载/加载配置中的 BGE-M3，并执行一次真实向量推理。"""

import asyncio
import json
import math
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import get_settings
from backend.app.infrastructure.embeddings import BGEM3EmbeddingProvider


async def main() -> None:
    provider = BGEM3EmbeddingProvider(get_settings())
    result = await provider.embed_query("Spark AQE 自适应查询执行")
    print(
        json.dumps(
            {
                "provider": provider.provider_name(),
                "model": result.model,
                "dimension": len(result.embedding),
                "norm": round(math.sqrt(sum(v * v for v in result.embedding)), 6),
                "device": provider.device,
                "cache_dir": str(provider.cache_dir),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
