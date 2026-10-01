"""Spider 数据加载（HuggingFace 镜像友好）。

数据源：
- xlangai/spider：train/validation 拆分，含 db_id / question / query（gold SQL）。
- richardr1126/spider-schema：按 db_id 的表结构定义。

按 db_id join 后，每行注入 schema_string，供数据集构建器使用。
产物缓存到 HF 本地缓存目录（datasets 库默认行为）。
"""

from __future__ import annotations

from pathlib import Path

from training.dataset_builder.common.records import ShareGPTRecord  # noqa: F401  (保持 API 一致)


class SpiderSource:
    """Spider 数据访问门面：统一暴露 train/dev 行（含 db_id/question/query/schema_string）。"""

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, list[dict]]:
        """返回 {"train": [...], "dev": [...]}。"""
        try:
            return self._load_from_hub()
        except Exception:
            return self._load_from_official_zip()

    def _load_from_hub(self) -> dict[str, list[dict]]:
        from datasets import load_dataset  # 延迟导入

        dataset = load_dataset("xlangai/spider")
        schema_ds = load_dataset("richardr1126/spider-schema", split="train")

        schema_by_db: dict[str, str] = {}
        for row in schema_ds:
            db_id = row.get("db_id")
            schema_text = row.get("Schema (values (type))")
            if db_id and schema_text:
                schema_by_db[db_id] = schema_text

        def with_schema(rows) -> list[dict]:
            result = []
            for row in rows:
                item = dict(row)
                item["schema_string"] = schema_by_db.get(item.get("db_id") or "", "")
                result.append(item)
            return result

        train = with_schema(dataset.get("train", []))
        dev = with_schema(dataset.get("validation", dataset.get("test", [])))
        return {"train": train, "dev": dev}

    def _load_from_official_zip(self) -> dict[str, list[dict]]:
        import json

        train_file = self.raw_dir / "spider" / "train.json"
        dev_file = self.raw_dir / "spider" / "dev.json"
        if not train_file.is_file() or not dev_file.is_file():
            raise FileNotFoundError(
                f"Spider 数据不可用：HF 加载失败且官方 zip 未找到（{self.raw_dir}/spider/）。"
            )
        return {
            "train": json.loads(train_file.read_text(encoding="utf-8")),
            "dev": json.loads(dev_file.read_text(encoding="utf-8")),
        }
