# DataPilot-AI 数据开发知识库

第一版 Knowledge Base 覆盖 Hive、Spark、Kafka、Flink、ClickHouse、MySQL、数据仓库、架构设计与面试题。文档用于 RAG 检索、数据开发问答、Text2SQL 背景增强、SQL Review 解释和面试准备。

## 统计

- 文档数量：42 篇正文文档，另含本 README。
- 中文总字数：48659 字。
- 分类目录：hive, spark, kafka, flink, clickhouse, mysql, datawarehouse, interview, architecture。

## 分类目录

### hive

- [hive_bucket.md](hive/hive_bucket.md)：1149 中文字。
- [hive_execution_flow.md](hive/hive_execution_flow.md)：1140 中文字。
- [hive_join.md](hive/hive_join.md)：1156 中文字。
- [hive_partition.md](hive/hive_partition.md)：1162 中文字。
- [hive_sql_optimization.md](hive/hive_sql_optimization.md)：1151 中文字。

### spark

- [spark_aqe.md](spark/spark_aqe.md)：1107 中文字。
- [spark_core.md](spark/spark_core.md)：1067 中文字。
- [spark_shuffle.md](spark/spark_shuffle.md)：1073 中文字。
- [spark_skew.md](spark/spark_skew.md)：1098 中文字。
- [spark_sql.md](spark/spark_sql.md)：1075 中文字。
- [spark_tuning.md](spark/spark_tuning.md)：1091 中文字。

### kafka

- [kafka_architecture.md](kafka/kafka_architecture.md)：1101 中文字。
- [kafka_consumer.md](kafka/kafka_consumer.md)：1101 中文字。
- [kafka_isr.md](kafka/kafka_isr.md)：1121 中文字。
- [kafka_offset.md](kafka/kafka_offset.md)：1119 中文字。
- [kafka_producer.md](kafka/kafka_producer.md)：1108 中文字。

### flink

- [flink_architecture.md](flink/flink_architecture.md)：1075 中文字。
- [flink_checkpoint.md](flink/flink_checkpoint.md)：1081 中文字。
- [flink_sql.md](flink/flink_sql.md)：1081 中文字。
- [flink_state.md](flink/flink_state.md)：1079 中文字。
- [flink_watermark.md](flink/flink_watermark.md)：1086 中文字。

### clickhouse

- [clickhouse_engine.md](clickhouse/clickhouse_engine.md)：1117 中文字。
- [clickhouse_mergetree.md](clickhouse/clickhouse_mergetree.md)：1116 中文字。
- [clickhouse_optimization.md](clickhouse/clickhouse_optimization.md)：1129 中文字。
- [clickhouse_orderby.md](clickhouse/clickhouse_orderby.md)：1118 中文字。
- [clickhouse_partition.md](clickhouse/clickhouse_partition.md)：1120 中文字。

### mysql

- [mysql_explain.md](mysql/mysql_explain.md)：1098 中文字。
- [mysql_index.md](mysql/mysql_index.md)：1122 中文字。
- [mysql_lock.md](mysql/mysql_lock.md)：1113 中文字。
- [mysql_optimization.md](mysql/mysql_optimization.md)：1122 中文字。
- [mysql_transaction.md](mysql/mysql_transaction.md)：1107 中文字。

### datawarehouse

- [ads.md](datawarehouse/ads.md)：1122 中文字。
- [dimension_model.md](datawarehouse/dimension_model.md)：1167 中文字。
- [dwd.md](datawarehouse/dwd.md)：1125 中文字。
- [dws.md](datawarehouse/dws.md)：1122 中文字。
- [kimball.md](datawarehouse/kimball.md)：1125 中文字。
- [ods.md](datawarehouse/ods.md)：1124 中文字。

### interview

- [data_engineer_top100.md](interview/data_engineer_top100.md)：2970 中文字。

### architecture

- [kappa_architecture.md](architecture/kappa_architecture.md)：1129 中文字。
- [lakehouse.md](architecture/lakehouse.md)：1126 中文字。
- [lambda_architecture.md](architecture/lambda_architecture.md)：1138 中文字。
- [medallion.md](architecture/medallion.md)：1128 中文字。

## 使用方式

- 可通过 DataPilot-AI 知识库上传功能索引这些 Markdown 文件。
- 推荐先索引基础主题，再索引 `interview/data_engineer_top100.md`。
- 检索时可按目录名作为领域标签，例如 `spark`、`hive`、`datawarehouse`。
- 每篇专题文档都包含代码示例、面试问题和性能优化经验，适合直接作为 RAG 原始材料。
