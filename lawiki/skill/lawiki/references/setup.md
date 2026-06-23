# 首次环境配置（setup）

agent 第一次在某台机器上用 lawiki、或检测到环境缺失时，照此走一遍。**每一步都让用户看到你在做什么**——尤其安装时明确说「正在安装环境……」。

lawiki 需要三样：① Python 3.11+（跑本 skill 自带的校验，零第三方依赖）② makeitdown（上游转换器，把各式文件转成 md）③ Obsidian（人看 wiki 用，可选、不自动装 GUI）。**可选第四样**：rag-retriever（语义检索，支撑交叉验证问答；不装则问答退化为「仅 wiki」，核心不受影响）。

## 第 0 步 · 一键安装（bundle 发布包，推荐）

若本 skill 来自 `lawiki-bundle`（解压后 `skill/lawiki` 旁有 `vendor/` 与 `install.py`），首选一键安装——它从 bundle 内 `vendor/` 本地装好 makeitdown 与 rag-retriever：

1. 先问用户选 **OCR 方式**（本地/云端，见第 2 步的对比表）。
2. 在 bundle 根目录跑（明确告诉用户"正在安装环境……"）：
   ```
   python install.py --ocr <local|cloud>
   ```
   （想先看将执行什么，可加 `--dry-run`。embedding 默认 local、离线、无需 key。）
3. 装完它会打印各部件结果与激活语。任一部件失败不阻塞核心（降级，见第 4 步）。

非 bundle 场景（只拿到 skill、或要手动控制）走下面第 1–5 步。

## 第 1 步 · 检测（并把结果告诉用户）

依次在 shell 跑，报告每项有无：
- `python --version` —— 需 3.11+。
- `uv --version` —— 装 makeitdown 用（没有也可后面装）。
- `makeitdown --help` —— 上游转换器。
- `rag-retriever --help` —— 可选，RAG 检索（没有则问答仅 wiki）。

## 第 2 步 · 让用户选 OCR 方式（装 makeitdown 前必问）

扫描件 / 图片需要 OCR。两种模式，**把这张优缺点对比给用户，让他选**：

| | 本地 PaddleOCR | 云端 PaddleOCR（AI Studio） |
|---|---|---|
| 转换时联网 | **不需要** | 需要 |
| 账号 / token | **不需要**，装完即用 | 需去百度 AI Studio 申请 |
| 隐私 | 文件**不出本机** | 文件**上传到百度服务器** |
| 费用 | 免费 | 可能按量计费 |
| 体积 / 速度 | 大（几百 MB）、转换较慢 | 小、装得快、转换较快 |

- **选本地** → 适合涉密案件、能接受慢一点、磁盘够。
- **选云端** → 适合要轻快、案件不涉高度机密、愿意联网。

### 选云端：申请 token（大陆可直连）

请用户点开 **百度 AI Studio 申请 API：https://aistudio.baidu.com/** ，拿到 token 后设环境变量：
```
PADDLEOCR_AISTUDIO_TOKEN=<你的token>
```
（或转换时传 `--cloud-token <token>`。）token 绝不写进任何文件、不提交。

## 第 3 步 · 安装（明确告诉用户「正在安装环境……」）

按所选 OCR 模式装 makeitdown（**大陆走 gitee 镜像 + 清华 PyPI 源**，避开卡顿）：

- **本地版**：
  ```
  uv tool install --python 3.12 --index https://pypi.tuna.tsinghua.edu.cn/simple "makeitdown[local] @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
  ```
- **云端版**：
  ```
  uv tool install --python 3.12 --index https://pypi.tuna.tsinghua.edu.cn/simple "makeitdown @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
  ```
- **缺 Python / uv**：先装 uv（Windows：`winget install astral-sh.uv`；macOS/Linux：`curl -LsSf https://astral.sh/uv/install.sh | sh`），uv 能顺带备好 Python。装系统软件可能要权限——装不动就把命令交给用户自己跑，别硬来。
- **本 skill 的校验（lint）**：零依赖，只要有 Python 即可，**无需安装**。
- `--python 3.12` 不可省：makeitdown 要求 `>=3.11,<3.13`，默认 3.13+ 的机器不钉版本会装失败。
- `--index 清华源` 仅为大陆加速依赖下载；海外可去掉。git 源已用 GitHub。

装完用 `makeitdown --help` 验证；提示用户命令找不到时跑 `uv tool update-shell` 后开新终端。

## 第 3 步补 · RAG 检索（可选，可降级）

支撑交叉验证问答。装与不装都行——不装则问答退化「仅 wiki」，核心（wiki + lint）零依赖不受影响。细节见 `rag.md`。

**装 rag-retriever**（让 `rag-retriever` 进 PATH；大陆走清华源加速依赖）：
```
uv tool install --python 3.12 --index https://pypi.tuna.tsinghua.edu.cn/simple "rag-retriever @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=rag-retriever"
```
（开发期也可不装、用环境变量 `LAWIKI_RAG_CMD='uv run --project "<本地路径>" rag-retriever'` 指向本地仓库，见 `rag.md`。海外可去掉 `--index`。）

**选 embedding 后端**（与 OCR 同样的「本地 vs 云端」权衡，把这张给用户选）：

| | `local`（fastembed） | `ollama`（bge-m3） | `openai`（硅基流动 bge-m3） |
|---|---|---|---|
| 联网 | **不需要**，离线 | 不需要（本地服务） | 需要 |
| 账号 / key | **不需要** | 不需要 | 需 API key |
| 中文质量 | 可用（bge-small-zh） | **最佳** | 最佳 |
| 适合 | 涉密 / 离线 | 有本地 ollama、要质量 | 要轻量、不介意联网 |

用环境变量选：`RAG_EMBED_BACKEND=local|ollama|openai`（默认 `local`）；`openai` 另需 `RAG_OPENAI_API_KEY`（硅基流动）。key 绝不写进文件、不提交。

**一致性铁规**：索引与查询**必须同一 embedding 模型**，否则相似度失真。机制：rag-retriever 索引时把模型记进 `.rag/`，wrapper 查询前自动比对、不一致即降级并提示 rebuild（删 `.rag/` 重建索引）。**换模型 = 必须重建索引。**

## 第 4 步 · 优雅降级

makeitdown 实在装不上时**不阻塞核心**：若用户已有别处转好的 `_md/`，lawiki 仍能直接 ingest + 跑校验，只是不能在本机做转换。把这点告诉用户。

RAG 同理可降级：没装 rag-retriever / 没建索引 / 模型不一致时，问答自动退化「仅 wiki」，并对用户挑明「当前无 RAG 交叉验证」。

## 第 5 步 · 告诉用户怎么激活

环境就绪后，明确告诉用户**以后怎么启动**：

> 把法律文件放进案件目录的 `原始资料/`，然后对我说下面任一句即可启动：
> 「**整理案件资料**」「**把案件资料建成 wiki**」「**建案件库 / 建案件 wiki**」「**处理这个案子**」「**ingest case files**」「**build a case wiki**」。
