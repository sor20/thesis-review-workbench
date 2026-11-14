---
name: review-workbench
description: 驱动本机「审阅工作台 / Review Workbench」完成无界面批量审阅。当用户要求读取工作台里的正文待改项、图表异常、待决问题或多方审校意见并批量判定，或要求导出结构化待改清单、让 AI 不逐条点击就完成审阅时使用。Use when a user asks to batch-review items in the Review Workbench, produce a decisions file, or export an actionable change list.
---

# 审阅工作台 · AI 驱动说明

本 skill 让调用方在对话中直接完成「读条目 → 判定 → 批量应用 → 拿结构化结果」，不需要人在页面上逐条点击。

工作台是**只读**工具：它不改 Word / PDF / 图片，只产出判定和待改清单。

## 前置条件

- Python 3.9+；仓库根目录下有 `index.html`、`server.py`。
- 命令均在仓库根目录执行（下文以 `review-workbench/` 表示该目录）。
- CLI 不需要启动服务。只有在需要看页面或调用 HTTP 接口时才启动：

```bash
python server.py serve --port 8770 --pages sample-pages
# Windows 无 python 时用：py -3 server.py serve --port 8770 --pages sample-pages
```

- 服务默认 `http://127.0.0.1:8770/`。端口被占用时换 `--port 8771`。
- 数据来自 `index.html` 内嵌的 JSON 块；`check` 可先确认能解析。

## 可用动作

| 动作 | 命令 | 作用 |
| --- | --- | --- |
| 自检 | `python server.py check` | 数据块能否解析、各类型条目数 |
| 列条目 | `python server.py list --type text` | 输出 JSON；`--type` 可为 `text/chart/decision/review/all` |
| 过滤 | `python server.py list --type chart --query 1-2` | 在条目 JSON 中做子串匹配 |
| 看单条 | `python server.py show D-01 --type decision` | 输出完整字段 |
| 生成模板 | `python server.py template --out out/decisions.json` | 把所有条目列成待填骨架 |
| 批量应用 | `python server.py apply --in out/decisions.json --out-dir out` | 校验 + 汇总 + 写 JSON 与 Markdown |
| 导出清单 | `python server.py export --in out/decisions.json --stdout` | 只输出 Markdown |
| HTTP 备用 | `GET /api/items?type=`、`POST /api/apply` | 服务已启动时使用 |

`apply` / `export` 的退出码：`0` 全部有效；`2` 有无效条目。**即使退出码为 2 也会写出结果**，`errors` 字段会指出是哪个 `type/id`、错在哪里。修正后重跑即可，不要改用页面逐条点击。

## 决定文件格式

最小可用形式（按类型分组）：

```json
{
  "schema": "review-workbench/decisions@1",
  "reviewer": "ai:claude",
  "text":     { "01":    { "decision": "通过",   "note": "已核对" } },
  "chart":    { "G-1-2": { "decision": "要改",   "note": "dpi 240" } },
  "decision": { "D-01":  { "choice": ["C"],      "note": "选 C" } },
  "review":   { "A-01":  { "decision": "采纳",   "note": "补限制条件" } }
}
```

也接受扁平数组，每项必须带 `type` 与 `id`：

```json
{ "items": [ { "type": "text", "id": "01", "decision": "通过", "note": "" } ] }
```

允许的取值：

| 类型 | 字段 | 取值 |
| --- | --- | --- |
| `text` | `decision` | `通过` / `要改` / `有疑问`（或 `agree` / `change` / `question`） |
| `chart` | `decision` | 同上 |
| `review` | `decision` | `采纳` / `不采纳` / `存疑`（或 `agree` / `reject` / `question`） |
| `decision` | `choice` | 选项 key；多选条目用数组，单选最多一个 |

`note` 可省略或留空字符串。ID 以 `list` / `template` 输出为准，不要自行改写（图表图的 ID 形如 `G-1-2`，表为 `T-1-1`，影调图版为中文 ID）。

## 标准流程

1. **自检**：`python server.py check`。若不是 25 条或解析失败，先停下报告，不要继续判定。
2. **读全量**：`python server.py list --type all > out/all-items.json`，或分四次按类型读取。
3. **逐条判定**（规则见下节），写一份 `decisions.json`。不要逐条询问用户。
4. **批量应用**：`python server.py apply --in decisions.json --out-dir out`。
5. **检查结果**：确认 `summary.errors == 0`；若不为 0，按 `errors[].type/id` 修正后重跑（最多两轮）。
6. **汇报**：给出 `summary`、`actionable` 条目、`unresolved` 清单和输出文件路径。

需要让用户拍板的条目，不要卡住流程：选一个最保守的选项并在 `note` 里写明「待确认」，或对文字类条目标 `有疑问`。

## 自主判定规则

判定必须基于条目自身字段，不得编造原文没有的信息。

**正文改动（text）**

- 有 `gap: true` 或依据里明确写「待复核 / 未定 / 缺少」→ `有疑问`。
- `level` 为 `必改`：若 `after` 已解决 `basis` 指出的问题 → `通过`；否则 → `要改`。
- `level` 为 `建议`：`after` 与 `before` 有实质差异且不引入新事实 → `要改`；只是措辞润色且可接受 → `通过`。
- `level` 为 `可忽略` 且无事实错误 → `通过`。

**图表（chart）**

- `anomaly: true` → `要改`，`note` 写具体异常项（直接引用 `anomalies`）。
- 无异常但图注与正文明显不符（条目里能看到）→ `有疑问`。
- 其余 → `通过`。

**待决问题（decision）**

- 优先采用条目里的 `advice` 对应的选项（如「建议 C」→ `C`）。
- 若 `advice` 缺失：单选时选「改动最小且可回退」的选项，多选时只选有明确依据的项。
- 选项会改变事实、编号或版式时，在 `note` 里写明影响面。
- 无法判断时仍要选一个可回退项，并在 `note` 标「待确认」，不要留空。

**多方审校（review）**

- `level` 为 `必改` 且 `rewrite` 不引入新事实 → `采纳`。
- `level` 为 `建议`：`rewrite` 是明确改进 → `采纳`；纯风格偏好 → `存疑`。
- 证据不足、需要原始统计表或原始文件才能确认 → `存疑`。
- 与已确认事实冲突 → `不采纳`，并在 `note` 写冲突点。

## 端到端示例（可直接复现）

```bash
cd review-workbench
python server.py check
# {"ok": true, ..., "items": 25, "byType": {"text": 8, "chart": 8, "decision": 4, "review": 5}}

python server.py apply --in examples/decisions-example.json --out-dir out
# summary.applied = 10, summary.errors = 0, 写出 review-result-*.json 与 待改清单-*.md

python server.py export --in examples/decisions-example.json --stdout
# 打印 Markdown：# 审阅待改清单
```

`examples/decisions-example.json` 已覆盖四类条目，可作为格式参照。首次给新数据做判定时，先 `template` 生成骨架，再只填需要判定的键。

## 输出解读

`apply` 的 JSON：

- `summary.itemsTotal / applied / byType / errors / unresolved`：总量与进度。
- `applied`：全部有效判定，按类型分组。
- `actionable`：只含需要继续处理的判定（`要改` / `有疑问` / `采纳` / `存疑`）。
- `errors`：无效条目，含 `type`、`id`、原因；无效项不会进入 `applied`。
- `unresolved`：本次未提交判定的条目 ID。
- `files`：写出的两个文件路径。

Markdown 按「正文改动 / 图表审阅 / 待决问题 / 多方审校」分组，只列 `actionable`，每条带位置、原文/现文或图注、选择项与备注。

## HTTP 备用通道

服务已启动时，可以完全不落盘地做同样的事：

```bash
curl -s http://127.0.0.1:8770/api/items?type=decision
curl -s -X POST http://127.0.0.1:8770/api/apply \
  -H "Content-Type: application/json" \
  --data-binary @decisions.json
```

`POST /api/apply` 默认 `write: true`，会在 `out/` 写文件；传 `"write": false` 则只返回 JSON。响应字段与 CLI 的 `apply` 一致。

## 失败与回退

| 现象 | 处理 |
| --- | --- |
| `index.html 中找不到数据块` | 确认在仓库根目录；用 `--index` 指向正确的 `index.html` |
| `JSON 解析失败` | 决定文件不是合法 JSON；重新生成，不要手工拼接字符串 |
| 退出码 2 且 `errors` 指向 unknown id | 用 `list` 重新确认 ID；图表 ID 有 `G-` / `T-` 前缀 |
| `选项不存在` | 用 `show <id> --type decision` 查看 `options[].k` |
| 单选条目传了多个选项 | 只保留一个，其余写进 `note` |
| `--pdf` 报缺少 PyMuPDF | 改用 `--pages <目录>`；或 `pip install pymupdf` |
| 端口被占用 | `--port 8771` |
| 页面图 404 | 启动时加 `--pages sample-pages`，或提供自己的页面 PNG 目录 |

回退原则：CLI 校验失败时不要反复盲改同一份文件；保留上一份可用结果，按 `errors` 逐条修正后重跑。若数据块本身缺失或损坏，停止并报告，不要伪造条目。

## 边界

- 只读原文：不得修改 `index.html` 的示例数据、`sample-pages/`、任何 Word/PDF/图片交付物。
- 不写浏览器存储：AI 无法把判定写进用户的 `localStorage`；CLI 结果与页面会话是两条独立通道。要让页面看到，只能由用户在页面导出后自行处理。
- 不编造证据：缺原始文件、缺统计表时标 `有疑问` / `存疑`，不要用推测补齐。
- 输出只写 `out/`（或 `--out-dir` 指定目录）；不得改写 `examples/`。
- 示例数据是虚构的，不能把示例的判定结果当作真实审阅结论。
