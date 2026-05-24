# 运行诊断与数据可靠性 1.0（Phase 2）

本文档记录 #1391 Phase 2 的后端落地范围：基于 Phase 1 的 `trace_id` 与 provider run 记录，生成用户可读的运行诊断摘要，并提供可复制的脱敏排障文本。

## 本轮范围

- 新增 `RunDiagnosticSummary` 聚合逻辑，输出总体状态：
  - `normal` / 正常
  - `degraded` / 部分降级
  - `failed` / 失败
  - `unknown` / 未知
- 摘要覆盖以下关键链路：
  - 实时行情
  - 日线数据
  - 新闻搜索
  - LLM
  - 通知
  - 历史保存
- `AnalysisService` 同步/异步任务结果追加可选 `diagnostic_summary`。
- 新增历史报告诊断 API：

```http
GET /api/v1/history/{record_id}/diagnostics
```

`record_id` 支持历史记录主键 ID 或 `query_id`，返回诊断摘要与 `copy_text`。

## 复制排障信息

`copy_text` 是面向 issue/排障的纯文本，包含：

- `trace_id`
- `query_id`
- `stock_code`
- `trigger_source`
- 总体 `data_status`
- 实时行情、日线、新闻、LLM、通知、历史保存的简短状态
- 首要原因

生成前会复用运行诊断脱敏规则，避免输出 token、API key、Authorization、Cookie、webhook URL、邮箱密码、代理凭据等敏感信息。

## 兼容性边界

- 本轮不新增配置项，不改变数据源优先级，不改变 fallback 策略。
- API 只追加可选字段和新增只读接口；旧客户端可忽略。
- 旧报告没有 `context_snapshot.diagnostics` 时返回 `unknown`，不报错。
- 通知诊断在当前任务上下文中记录；历史报告如果保存时尚无通知证据，会在摘要中显示通知结果未知。
- 诊断摘要生成失败不得影响报告读取或分析主流程。

## 验证建议

```bash
python -m pytest tests/test_run_diagnostics_p2.py tests/test_run_diagnostics_p1.py
python -m py_compile src/services/run_diagnostics.py src/services/history_service.py api/v1/endpoints/history.py api/v1/schemas/history.py
```
