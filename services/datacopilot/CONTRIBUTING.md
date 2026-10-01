# 参与贡献

## 开发环境

```bash
conda env create -f environment.yml
conda activate datacopilot
python -m pytest -q
```

复制 `.env.example` 为 `.env`，密钥只保存在本地，不得提交到仓库。

## 分支流程

1. 从最新主分支创建功能分支。
2. 每次提交只解决一个明确问题。
3. 修改行为时同步补充测试和中文文档。
4. 提交前运行全量测试和 `docker compose config --quiet`。

## 架构约束

- 表现层不得直接访问 ChromaDB、文件系统或大模型接口。
- 应用层依赖领域端口，不直接依赖具体基础设施实现。
- 工具输入必须使用 Pydantic 模型和 JSON Schema 校验。
- 新增 Provider 时实现统一的 `LLMProvider` 或 `VectorStore` 端口。
- 不在业务代码中硬编码密钥、绝对路径和生产地址。

## 提交格式

```text
feat: 新增功能
fix: 修复问题
docs: 更新文档
test: 补充测试
refactor: 重构但不改变行为
```

## 合并请求检查

- [ ] 代码和提交内容聚焦于当前任务。
- [ ] 新增说明、注释和界面文案使用中文。
- [ ] 接口兼容性已经确认。
- [ ] 单元测试和集成测试通过。
- [ ] Docker 配置可以正常解析。
- [ ] 文档准确区分已实现能力和规划能力。
