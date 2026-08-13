# Invoice Risk Auditor for Codex

一个面向中文电子普通发票的 Codex Skill。它在本地完成 OCR、字段结构化、供应商历史匹配和确定性规则检测，并输出可追溯的 JSON 风险报告。

## 主要能力

- 读取 PNG、JPG、TIFF、BMP 或 PDF 发票。
- 使用 PaddleOCR 在本地识别中文发票内容。
- 提取发票号码、日期、购销双方、商品、数量、单价、金额、税额和价税合计。
- 使用商品别名表统一商品名称。
- 按供应商、商品类别和商品名称匹配企业历史数据。
- 执行 R001–R007 确定性风险规则。
- 区分“异常”与“需要人工复核”。
- 输出 OCR、字段提取和最终分析三份 JSON 证据文件。
- 不调用外部大模型 API；由 Codex 基于确定性结果生成中文解释。

## 安装为仓库级 Skill

将本仓库克隆到工作区后，Codex 会从 `.agents/skills/invoice-risk-auditor/` 发现该 Skill。

创建独立虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\.agents\skills\invoice-risk-auditor\scripts\requirements.txt
```

PaddleOCR 首次运行时需要下载模型文件。模型会进入本机缓存，不会写入仓库。

## 在 Codex 中使用

可以直接对 Codex 说：

```text
使用 $invoice-risk-auditor 分析这张发票，并结合我提供的企业历史数据输出可核查的风险结论。
```

也可以直接运行脚本：

```powershell
.\.venv\Scripts\python.exe .\.agents\skills\invoice-risk-auditor\scripts\analyze_invoice.py `
  --invoice .\.agents\skills\invoice-risk-auditor\assets\sample-data\sample_invoice.png `
  --supplier-history .\.agents\skills\invoice-risk-auditor\assets\sample-data\supplier_history.xlsx `
  --product-aliases .\.agents\skills\invoice-risk-auditor\assets\sample-data\product_alias.csv `
  --events .\.agents\skills\invoice-risk-auditor\assets\sample-data\invoice_events.xlsx `
  --events-sheet 开票事件日志 `
  --output-dir .\outputs\sample
```

示例文件均为模拟数据，只用于功能验证。

## 输入数据

- 发票图片或 PDF。
- 供应商历史汇总 Excel。
- 商品别名 CSV。
- 可选的历史开票事件 Excel，用于 R007 频率风险。

字段规范见 [data-contract.md](.agents/skills/invoice-risk-auditor/references/data-contract.md)，规则定义见 [rule-catalog.md](.agents/skills/invoice-risk-auditor/references/rule-catalog.md)。

## 安全与边界

- 本项目做风险筛查和辅助审核，不提供官方发票验真或法律结论。
- 风险结论由确定性规则产生，Codex 只解释已有证据，不改变触发规则。
- 请勿将真实发票、未脱敏企业数据、API Key 或本地 `.env` 提交到公开仓库。
- 当前字段提取器主要适配中文电子普通发票常见版式；模糊拍照、复杂多行明细和其他票种可能需要扩展。

## License

[MIT](LICENSE)
