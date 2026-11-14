# 审阅工作台 Review Workbench

一个跑在本机的**只读审阅工作台**：把长文档的待改项、图表、待决问题和多方审校意见集中到一个浏览器页面里逐条判定，并把结论导出成 JSON / Markdown，交给后续流程或 AI 汇总。

前端是单个自包含的 `index.html`（原生 JavaScript，无构建步骤），后端是一个只依赖 Python 标准库的本地服务 `server.py`。审阅记录只保存在浏览器的 `localStorage` 里，不会上传到任何地方。

> 内置数据全部是虚构的示例，用于演示交互，不代表任何真实文档。

---

## 它解决什么问题

长文档改到后期，问题通常散落在很多地方：

- 正文改写意见、图表规格异常、需要作者拍板的问题、多个审校来源的重复意见，分别躺在不同的表格或对话里；
- 逐条确认要来回切换文件，容易漏项，也难以统计「还剩多少没看」；
- 判定结果需要留痕，但又不希望工具直接改动交付文件。

这个工作台把四类条目放在同一套界面里：

| 标签页 | 内容 | 判定方式 |
| --- | --- | --- |
| 正文改动 | 原文 / 现文对照 + 依据 | 通过 / 要改 / 有疑问 |
| 图表审阅 | 页面图、图注、有效 dpi、最小字号、异常项 | 通过 / 要改 / 有疑问 |
| 待决问题 | 题干、选项、建议与理由 | 选择一个或多个选项 |
| 多方审校 | 多个来源对同一段落的意见、引文与建议改写 | 采纳 / 不采纳 / 存疑 |

判定只写入浏览器本地；点「导出」后，服务端把 JSON 与 Markdown 落到 `out/`，也可以由 AI 通过命令行批量生成同样的结果。

---

## 快速开始

环境要求：Python 3.9+（只用标准库）。可选的 PDF 页面渲染需要 `pip install pymupdf`。

### Windows

双击 `start.bat`，或：

```bat
python server.py serve --port 8770 --pages sample-pages --open
```

### macOS / Linux

```bash
python3 server.py serve --port 8770 --pages sample-pages --open
```

然后访问 <http://127.0.0.1:8770/>。默认只监听 `127.0.0.1`，不对外网开放。

### 换成自己的数据

工作台的数据内嵌在 `index.html` 的五个 `<script type="application/json">` 块里：

| 数据块 | 用途 |
| --- | --- |
| `dataset` | 正文改动条目 |
| `figdata` | 图表条目（`figs` 与 `tables`） |
| `tonedata` | 影调诊断图版 |
| `decdata` | 待决问题 |
| `batch125` | 多方审校条目与模型列表 |

替换这些 JSON 即可，界面无需改动。字段可以直接参考示例数据的结构；`server.py list --type ...` 会输出服务端解析后的扁平条目，便于确认字段是否齐全。

页面图有两种来源：

- `--pages <目录>`：读取 `p001.png`、`1.png` 或 `page-1.png` 命名的页面图；
- `--pdf <文件>`：用 PyMuPDF 按页码渲染（`/page/<n>?dpi=150`）。

两者都没有时，图表审阅仍可打开，但页面区域会提示缺少页面源。

---

## 审阅与导出的完整流程

1. 启动服务，打开页面。
2. 在「正文改动」里用类别 / 级别 / 状态筛选，逐条看原文与现文。快捷键：`1` 通过、`2` 要改、`3` 有疑问；方向键切换条目。
3. 在「图表审阅」里切换「影调诊断 / 只看异常 / 全部」，查看页面图与异常项。
4. 在「待决问题」里选择选项，备注会自动保存。
5. 在「多方审校」里按来源筛选，对每条意见给采纳 / 不采纳 / 存疑。
6. 点右上角「导出」：浏览器下载一份 JSON，同时服务端在 `out/` 写入 `review-export-*.json` 与 `待改清单-*.md`。

**重要：** 判定只存在当前浏览器的 `localStorage`，服务端读不到，别的浏览器或另一台机器也读不到。要让外部程序汇总，必须先在页面里点「导出」，或改用下面的 CLI 流程。

---

## 无界面（AI / 脚本）用法

CLI 不依赖浏览器，读取同一份 `index.html` 数据，直接产出结构化结果。

```bash
# 1. 自检：数据块能解析、条目计数
python server.py check

# 2. 读条目（JSON）
python server.py list --type text
python server.py list --type chart --query 1-2
python server.py show D-01 --type decision

# 3. 生成决定文件模板（把所有条目列成待填的骨架）
python server.py template --out out/decisions.json

# 4. 批量应用：校验 + 汇总 + 写出 JSON 与 Markdown
python server.py apply --in out/decisions.json --out-dir out

# 5. 只导出 Markdown
python server.py export --in out/decisions.json --stdout
```

退出码：`0` 全部校验通过；`2` 存在无效条目（结果仍会写出，`errors` 字段列出原因，修正后重跑即可）。

### 决定文件格式

```json
{
  "schema": "review-workbench/decisions@1",
  "reviewer": "ai:my-agent",
  "text": {
    "01": { "decision": "通过", "note": "已核对" }
  },
  "chart": {
    "G-1-2": { "decision": "要改", "note": "dpi 240" }
  },
  "decision": {
    "D-01": { "choice": ["C"], "note": "选 C" }
  },
  "review": {
    "A-01": { "decision": "采纳", "note": "补限制条件" }
  }
}
```

- `text` / `chart` 接受 `通过`、`要改`、`有疑问`（也接受 `agree` / `change` / `question`）。
- `review` 接受 `采纳`、`不采纳`、`存疑`（也接受 `agree` / `reject` / `question`）。
- `decision` 的 `choice` 是选项 key；多选条目传数组，单选条目最多一个。
- 也可以用扁平的 `items` 数组，每项带 `type` 与 `id`：

```json
{ "items": [ { "type": "text", "id": "01", "decision": "通过", "note": "" } ] }
```

结果文件包含：

```json
{
  "summary": { "itemsTotal": 25, "applied": 10, "byType": {}, "errors": 0, "unresolved": 15 },
  "applied": {},
  "actionable": {},
  "errors": [],
  "unresolved": [],
  "files": []
}
```

`actionable` 只保留需要继续处理的判定（要改 / 有疑问 / 采纳 / 存疑），`unresolved` 是尚未判定的条目。

---

## HTTP 接口

服务启动后可用：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 工作台页面 |
| GET | `/health` | `{"ok":true,"items":25}` |
| GET | `/api/items?type=&q=` | 条目列表 |
| GET | `/api/items/<id>` | 单条 |
| GET | `/page/<n>?dpi=150` | 页面图（PNG） |
| GET | `/media/<相对路径>` | 内嵌原图（需 `--media`） |
| POST | `/api/apply` | 请求体即决定文件；默认写盘，返回结构化结果 |
| POST | `/api/export` | 请求体 `{"json": {...}, "markdown": "# ..."}`，由页面导出按钮调用 |
| POST | `/api/template` | 返回空白决定文件 |

AI 只需要 `/api/items` 与 `/api/apply` 两个端点，就能完成「读入 → 判定 → 拿结果」的闭环。

---

## 给 AI 用的 skill

`skill/SKILL.md` 描述了 AI 在对话中直接驱动工作台的完整流程、命令、输入输出格式、失败回退与只读边界。把该目录装进支持 skill 的运行时即可；核心是让 AI 不逐条询问，而是：

1. 读条目；
2. 按条目里的级别、依据、异常项做判断，证据不足时标「有疑问 / 存疑」而不是编造；
3. 写一份决定文件；
4. 调 `apply` 拿结构化结果与待改清单。

---

## 目录结构

```
review-workbench/
├── index.html           # 自包含前端（样式 + 逻辑 + 示例数据）
├── server.py            # 本地服务 + CLI（check/list/show/template/apply/export）
├── start.bat            # Windows 启动脚本
├── start.sh             # macOS / Linux 启动脚本
├── sample-pages/        # 18 张中性示例页面图（p001.png … p018.png）
├── examples/
│   └── decisions-example.json   # 可直接 apply 的决定文件示例
├── skill/
│   └── SKILL.md         # AI 调用说明
├── out/                 # 运行时输出（已 gitignore）
├── README.md
├── LICENSE
└── .gitignore
```

---

## 示例数据说明

- 正文 8 条、图表 8 条（3 图 + 2 表 + 3 影调）、待决问题 4 条、多方审校 5 条，共 25 条。
- 所有文字、页码、数值、图注均为虚构；`sample-pages/` 是程序绘制的占位页面，不含任何真实文档内容。
- 示例里刻意保留了 1 条图表异常项（dpi 240、最小字号 6.2 pt），用于演示「只看异常」。
- `examples/decisions-example.json` 覆盖四类条目，执行 `apply` 后应得到 `applied: 10`、`errors: 0`。

---

## 已知限制

- **浏览器判定不落服务端。** 前端只写 `localStorage`，服务端无法读取；必须通过页面「导出」或 CLI 才能汇总。CLI 生成的结果也不会反向写入浏览器页面。
- **工具只读原文。** 它不改 Word、PDF、图片或任何交付文件，只产出判定与待改清单。
- **不做身份认证。** 默认绑定 `127.0.0.1`；若改为 `0.0.0.0`，同网段任何人都能读取条目并写入 `out/`。
- **页面图需要自己提供。** 没有 `--pages` 或 `--pdf` 时，图表审阅只有文字信息；`clip` 裁切参数目前只在 PDF 渲染模式下生效。
- **同名导出会覆盖。** 输出文件名精确到秒，同一秒内重复导出会覆盖前一份。
- **示例判定不代表质量结论。** 内置数据只用于演示交互和接口，不能当作审阅标准。

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
