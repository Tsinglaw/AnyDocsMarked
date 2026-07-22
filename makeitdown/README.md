# makeitdown

把一整个文件夹的文档**批量转换为高保真 Markdown**，作为 LLM 知识库的原材料。中国大陆可用，无需海外服务。

> 🤖 **完全不懂技术？** 去 [AnyDocsMarked Releases](https://github.com/Tsinglaw/AnyDocsMarked/releases) 下载
> 发布包，解压后把整个文件夹（或里面的 `SKILL.md`）交给你的 AI 助手，
> 说一句"帮我用 makeitdown 把这个文件夹的文档转成 markdown"即可——助手会自动安装并运行。
> 详见包内 `给你的AI助手.md`。（没有 Release 时，点 `Code → Download ZIP` 把整个仓库发给助手也行。）

基于 [microsoft/markitdown](https://github.com/microsoft/markitdown)（原生格式）与 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)（扫描件/图片）。

## 它做什么

- 递归扫描输入目录，按文件类型自动路由：
  - **原生文档**（Word/Excel/PPT、HTML、csv/json/xml、txt/md、epub、**有文字层的 PDF**）→ markitdown
  - **扫描件 / 图片型 PDF / 图片**（png/jpg/bmp/tiff…）→ PaddleOCR
  - **老式 .doc / .wps** → 先嗅探内核：实为 .docx 的直接转；真二进制用已装的 Word/WPS 或
    LibreOffice（见下文「老式 .doc / .wps」）
- 输出**镜像输入目录结构**的 `.md`，每个文件带 YAML frontmatter（来源、引擎、原件 SHA-256、正文 SHA-256 等），便于溯源与篡改检测。
- 单文件出错不中断整批，结果汇总到 `report.json`。

PDF 是否走 OCR：用 PyMuPDF 检测文字层——每页平均可提取字符数低于阈值（默认 50）即判为扫描件。

## 安装（国内网络优化）

需要 **Python 3.11 或 3.12**；用本地版的话建议 **3.11**（PaddlePaddle 对 3.11 支持最稳）。先有 Python 再装 uv：

```bash
pip install uv -i https://mirrors.aliyun.com/pypi/simple
```

然后**二选一**——两个版本的区别：

| | 本地版 | 云端版 |
|---|---|---|
| 联网 | 转换时**不需要** | 需要 |
| 账号 / token | **不需要**，装完即用 | 需去[百度 AI Studio](https://aistudio.baidu.com/paddleocr)拿 token |
| 隐私 | 文档**不出本机** | 文档上传到百度服务器 |
| 费用 | 免费 | 可能按量计费 |
| 体积 / 速度 | 大（几百 MB），转换较慢 | 小、装得快、转换较快 |

```bash
# 本地版（离线、免费、私密；体积大）
uv tool install --python 3.11 --index https://mirrors.aliyun.com/pypi/simple \
  "makeitdown[local] @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"

# 云端版（轻快；需联网 + token）
uv tool install --python 3.11 --index https://mirrors.aliyun.com/pypi/simple \
  "makeitdown @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
```

装完执行 `makeitdown --help` 验证。命令找不到时运行 `uv tool update-shell` 后开新终端。

说明：
- 依赖可走**阿里云 PyPI 镜像**；源码以 AnyDocsMarked monorepo 的 `makeitdown/` 子目录为权威来源。
- uv 自动下载 Python 是从 GitHub 拉的，国内可能慢——所以请先备好 Python 3.11；若 uv 仍有问题，用纯 pip 后备：
  ```bash
  pip install "makeitdown @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown" \
    -i https://mirrors.aliyun.com/pypi/simple
  ```
- **海外用户**：去掉 `--index`/`-i` 参数即可。若阿里云镜像本身连不上，同样去掉镜像参数落回默认 pypi.org。

## 使用

```bash
makeitdown <输入目录> -o <输出目录>
```

输出目录默认为 `<输入目录>_md`；`report.json` 默认写入输出目录。输出目录不得位于输入目录内部，避免重跑时递归转换旧输出。

转换过程逐文件把进度打到 stderr（`[k/N] ✓ 文件路径 (耗时)`，✗ 为失败并附错误）。批量大或走云端 OCR 时建议后台运行并 tail 日志；**完成以 `report.json` 为准**。

### OCR 后端：云端默认 + 显式同意

makeitdown 默认走**云端 OCR**（开箱即用、无需重型安装），但**绝不静默上传**：必须显式同意才会把文档传到云端服务。

- 同意上云：设置 token（`PADDLEOCR_AISTUDIO_TOKEN`）并加 `--cloud-consent`（或环境变量 `MAKEITDOWN_CLOUD_CONSENT=1`）。
- 不希望上传（本机性能足够）：加 `--ocr-engine local`（需安装本地版），文档不出本机。

**双 OCR 互校**（`--ocr-cross-check`，法律高危件用）：用 Paddle + MinerU 两个独立引擎比对，分歧（尤其金额/日期）标记 `quality: suspect`。校验方 MinerU 用 `--cross-check-mode {cloud,local,auto}` 选择：cloud 需 `MINERU_API_TOKEN` + 同意；local 需本机安装 `mineru`；auto 优先本地、否则云端、都没有则干净跳过。

### 常用选项

| 选项 | 说明 |
|---|---|
| `-o, --output DIR` | 输出目录（默认 `<输入>_md`） |
| `--ocr-engine {local,cloud,auto}` | OCR 后端（默认 `cloud`） |
| `--cloud-consent` | 显式同意把文档/文本发送到外部 OCR 或标题 LLM（或环境变量 `MAKEITDOWN_CLOUD_CONSENT=1`） |
| `--ocr-model NAME` | 本地模型（默认 `PP-StructureV3`，可选 `PaddleOCR-VL`） |
| `--cloud-token TOKEN` | PaddleOCR 云端 token（默认读环境变量 `PADDLEOCR_AISTUDIO_TOKEN`） |
| `--ocr-cross-check` | 启用双 OCR 互校（Paddle + MinerU 比对，需可选依赖） |
| `--cross-check-mode {cloud,local,auto}` | MinerU 互校引擎（默认 `cloud`；cloud 需 `MINERU_API_TOKEN` + 同意；local 需本机安装 `mineru`；auto 优先本地） |
| `--workers N` | 并发数（默认按 CPU 核数） |
| `--skip-existing` | 输出比源文件新则跳过（轻量增量） |
| `--text-threshold N` | PDF 判定为扫描件的每页平均字符数阈值（默认 50） |
| `--report PATH` | report.json 路径 |
| `--no-quality-check` | 关闭输出质检（所有产出按正常处理） |
| `--warn-min-chars N` | 非空白字符数低于此值则警告（默认 20） |
| `--warn-min-chars-per-page N` | 多页文档每页平均字符数低于此值则警告（默认 50） |
| `--warn-garbled-ratio F` | 乱码字符比例超过此值则警告（默认 0.02） |
| `--warn-repeat-count N` | 某行重复超过此次数则警告（默认 30） |
| `--warn-min-confidence F` | OCR 区域识别置信度低于此值则警告（0-1，默认 0.6；仅本地 PP-StructureV3 暴露置信度时生效） |

### 质检与异常警告

转换**成功但结果可疑**的文件（整页空白、乱码、内容异常重复、多页却几乎没字、**OCR 识别
置信度过低**），不会被当作正常结果悄悄写出，而是会被**标记**——既进 `report.json` 的
`warnings`，也写进该 `.md` 的 frontmatter（`quality: suspect` + `warnings` 列表），警告随
文件进入下游知识库。

> 置信度检测专为"OCR 局部数字损坏"这类法律高危盲点设计：本地 PP-StructureV3 会给出每个
> 识别区域的置信度，低于阈值即标记。云端 PaddleOCR-VL 不提供逐区域置信度，该项对其自动
> 跳过（不影响其他质检规则）。

- **只警告、不改内容**：质检永不修改或删除你的转换结果，最坏只是误报一个标记。
- **硬失败**（转换直接报错）仍按原样隔离：不产出 `.md`，记入 `report.json` 的 `failures`，
  单文件出错不中断整批。
- 阈值都可用上面的 `--warn-*` 选项调整；`--no-quality-check` 可整体关闭。

`report.json` 中 `succeeded` 与 `warned` 互斥：前者是产出且干净，后者是产出但可疑。

### LLM 标题层级重建（可选，默认关）

扫描件经 OCR 出来的是**扁平文本**，几乎没有 `#` 标题层级，下游知识库只能平铺。开启
`--structure-headings` 后，会用一个 LLM **只为 OCR 产物**重建标题层级（native/Word 等
本就带层级，不处理）。

**安全第一**：LLM **只返回"行号 → 标题级别"的数字**，绝不经手正文——加 `#` 由本地完成，
正文逐字节原样保留。因此该功能**在原理上不可能改动正文内容**（金额/日期/当事人零风险）。
非层级材料（聊天记录、清单、表单）会被判为"无标题"并保持扁平；标题占比异常高时整份回退
扁平并标记。任何失败都回退原文、绝不丢转换结果。

该功能会把候选标题行发送给配置的 OpenAI 兼容端点，因此同样必须显式加 `--cloud-consent`：

```bash
# PowerShell；key 绝不硬编码，从环境变量读
$env:MAKEITDOWN_LLM_BASE_URL = "https://api.deepseek.com/v1"
$env:MAKEITDOWN_LLM_MODEL    = "deepseek-chat"
$env:MAKEITDOWN_LLM_API_KEY  = "你的key"
makeitdown docs --ocr-engine local --structure-headings --cloud-consent
```

| 选项 | 说明 | 默认 |
|---|---|---|
| `--structure-headings` | 开启 OCR 产物的标题层级重建 | 关 |
| `--llm-base-url URL` | OpenAI 兼容端点 | 环境变量 `MAKEITDOWN_LLM_BASE_URL` |
| `--llm-model NAME` | 模型名 | 环境变量 `MAKEITDOWN_LLM_MODEL` |
| `--llm-api-key KEY` | API key | 环境变量 `MAKEITDOWN_LLM_API_KEY` |
| `--llm-max-heading-len N` | 候选标题行最大长度 | 80 |
| `--llm-max-lines N` | 候选行超过则跳过结构化 | 1500 |
| `--llm-max-heading-ratio F` | 标题占比超过则判无层级、回退扁平 | 0.35 |

成功结构化的文件 `engine` 会追加后缀（如 `local:pp-structurev3+llm-heads:deepseek-chat`），
数量计入 `report.json` 的 `structured`。

### 老式 .doc / .wps

`.doc`（老 Word 二进制）和 `.wps`（金山）markitdown 读不了，makeitdown 会分层处理：

1. **内容嗅探（零依赖）**：很多"`.doc`/`.wps`"其实是改了后缀的 `.docx`（OOXML）——直接当
   `.docx` 转，无需任何外部工具。
2. **已装的 Word / WPS（Windows）**：真二进制文件，调用本机**已安装**的 Word 或金山 WPS
   转换。需装可选依赖 `makeitdown[com]`（只装 COM 桥，不装 Office）：
   ```bash
   uv tool install --python 3.11 --index https://mirrors.aliyun.com/pypi/simple \
     "makeitdown[com] @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
   ```
3. **LibreOffice（可选）**：若 `soffice` 已在 PATH，则用它转换（跨平台）。makeitdown
   **不会自动安装 LibreOffice**；需要的话请自行安装（国内走清华/中科大镜像）。
4. **都没有**：该文件不会被硬转成垃圾，而是**干净跳过**并记入 `report.json` 的 `skipped`，
   附上"怎样才能转成功"的说明。

> makeitdown 自身从不静默安装任何外部程序；要不要装 LibreOffice 完全由你决定。

## 输出示例

```markdown
---
source: 合同/2024采购框架.pdf
source_type: pdf
engine: cloud:paddleocr-vl-1.6
pages: 12
converted_at: 2026-06-15T10:30:00
provenance_version: 1
source_sha256: <原始 PDF 的 SHA-256>
content_sha256: <下方 Markdown 正文的 SHA-256>
---

# 采购框架协议
...
```

## 配合 LLM 知识库

转换完成后，把构建 wiki 的工作流指向 `<输出目录>`——干净的 Markdown + frontmatter 正适合 LLM 增量消化、交叉引用。仓库内也提供了一个薄 skill（`skill/makeitdown/SKILL.md`），让 agent 知道何时调用本工具。

## 开发（从源码）

```bash
git clone https://github.com/Tsinglaw/AnyDocsMarked.git
cd AnyDocsMarked/makeitdown
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]" -i https://mirrors.aliyun.com/pypi/simple
.venv/Scripts/python -m pytest -q
```

> Windows 用 `.venv/Scripts/python`；macOS/Linux 用 `.venv/bin/python`。
