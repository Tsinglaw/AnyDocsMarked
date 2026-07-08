# 设计：并行发布"预装 embedding 的离线包"(v1.1.2 双 bundle)

日期：2026-07-08
状态：已批准设计，待写实现计划
范围：`rag-retriever/`(提交已写好的离线加载 WIP)、`lawiki/scripts/build_bundle.py`、`.github/workflows/release.yml`、若干文档。makeitdown、lawiki skill 运行逻辑不变。

## 背景与目标

v1.1.1 的 release 只有源码小包(~500KB),终端用户首次建索引时 rag-retriever 默认后端 fastembed 需从 **HuggingFace 下载 embedding 模型**(境外,国内常慢/失败)。用户希望**并行发一个"预装 embedding"的离线包**,与小包同时挂在同一 release 供选择。

**离线加载路径已在工作区 WIP 中完整实现(未提交)**,本设计的新增工作很窄:让构建产出两个包 + 提交这套 WIP + 少量文档/测试。

**已在 WIP 中实现(本设计负责提交,不重写)**:
- `rag_retriever/embed.py`：`LocalEmbedder` 优先加载 vendored `_models/<model>`（fastembed `specific_model_path` + `local_files_only=True`），否则回退在线下载。
- `rag_retriever/chunk.py`：存在 vendored `_tiktoken` 时设 `TIKTOKEN_CACHE_DIR`，token 计数离线。
- `rag-retriever/pyproject.toml`：`[tool.hatch.build.targets.wheel] artifacts = ["rag_retriever/_models/**", "rag_retriever/_tiktoken/**"]`，把 vendored 资产强制打进 wheel（`uv tool install` 从源码构建时即带模型）。
- `rag-retriever/.gitignore`：忽略 `rag_retriever/_models/`、`rag_retriever/_tiktoken/`。
- `rag-retriever/scripts/fetch_bundled_model.py`：发版前在联网机上跑一次，下载 embedding ONNX → `_models/`、tiktoken → `_tiktoken/`。

**范围边界**：只预装 **embedding(`BAAI/bge-small-zh-v1.5`)+ tiktoken**（即"预装embedding"）。**opt-in 重排模型 `bge-reranker-v2-m3` 不预装**——用户若开 `RAG_RERANK=local` 仍会联网下 HF，作为**已文档化的限制**，不在本设计扩展。

## 版本策略

发**新版本 v1.1.2**（离线机制需提交新代码，与 v1.1.1 的 tag 不一致，不回补旧 release）。同一 release 挂两个产物：
- `anydocsmarked-v1.1.2.zip`（源码小包，≈500KB，同今日行为）
- `anydocsmarked-v1.1.2-offline.zip`（源码 + vendored embedding ONNX ≈90MB + tiktoken）

## 组件 1：`lawiki/scripts/build_bundle.py` 双模式

- **默认排除 vendored 资产**：把 `_models`、`_tiktoken` 加入按名排除集（`_ignore` 已按名匹配任意层级），保证即便本地开发者跑过 `fetch`，普通包仍是纯源码小包。
- **新增 `--offline` 开关**：
  1. 该模式下**不**排除 `_models`/`_tiktoken`（把它们纳入 vendor 拷贝）；
  2. 产物命名 `anydocsmarked-v<ver>-offline.zip`；
  3. **前置校验**：`--offline` 但 `rag-retriever/rag_retriever/_models` 不存在或为空 → `sys.exit(非0)` 报错，防止 CI 静默产出"空壳离线包"。
- 其余内容（skill + vendor 源码 + install.py + MANIFEST + README.txt）两模式一致。

## 组件 2：`.github/workflows/release.yml` 产出并挂载两个包

`on: push tags v*`，`permissions: contents: write`。步骤：
1. checkout（`fetch-depth: 0`）、setup-python 3.12。
2. **先构建小包**（此时 `_models` 尚未 fetch）：`python lawiki/scripts/build_bundle.py --version "${GITHUB_REF_NAME#v}"`。
3. **vendor 离线资产**：`pip install ./rag-retriever` → `python rag-retriever/scripts/fetch_bundled_model.py`。
4. **构建离线包**：`python lawiki/scripts/build_bundle.py --version "${GITHUB_REF_NAME#v}" --offline`。
5. **校验离线包**（决定性、便宜）：解压列表断言含 `vendor/rag-retriever/rag_retriever/_models/` 下的 `*.onnx`；缺失即令 job 失败（不发布残包）。
6. `softprops/action-gh-release@v2` 挂载两个 zip：`lawiki/dist/anydocsmarked-*.zip`，`generate_release_notes: true`。

## 组件 3：提交 WIP

作为离线包能工作的前提，提交工作区已写好的 5 处改动：`embed.py`、`chunk.py`、`pyproject.toml`、`.gitignore`、新增 `rag-retriever/scripts/fetch_bundled_model.py`。提交前跑 rag-retriever 测试确认这些改动不破坏现有行为。

> 注：工作区还有**其它无关的**未提交改动（如 makeitdown/README、ocr_mineru.py、lawiki/install.py 等）——**只提交与本离线特性相关的上述文件**，其余保持未提交。

## 组件 4：文档

- `lawiki/install.py`：安装结束提示补一句——检测到 bundle 内已带 vendored `_models` 时，说明"本机将离线加载 embedding，无需下载"；否则提示普通包首次建索引会联网下 `bge-small-zh-v1.5`（国内可设 `HF_ENDPOINT=https://hf-mirror.com` 加速）。
- `lawiki/skill/lawiki/references/setup.md` 与根 `README.md`：加一小节"两种发布包"——离线包(解压即用、适合国内/内网)vs 小包(需联网下模型);并注明重排模型仍需联网(若开启)。
- 保持简洁,不重写既有文档结构。

## 测试

- **`build_bundle` 单元测试**（新增 `lawiki/scripts/test_build_bundle.py`，stdlib unittest，用一个**假的**几字节 `_models/.../model.onnx` 占位，不下真模型）：
  - 默认模式：产出 zip **不含** `_models`/`_tiktoken`。
  - `--offline`：产出 zip **含** vendored 假模型文件，且文件名以 `-offline.zip` 结尾。
  - `--offline` 但 `_models` 缺失/空 → 退出码非 0。
- **`embed` vendored-load 测试**（`rag-retriever/tests/`，monkeypatch fastembed，不下真模型）：当 `_bundled_model_dir(model)` 存在时，`LocalEmbedder` 以 `specific_model_path` + `local_files_only=True` 构造；不存在时走普通下载分支。
- 现有 rag-retriever / makeitdown / lawiki 测试全部仍通过。

## 交付定义

- `build_bundle.py`：默认排除 + `--offline`（含命名、前置校验）。
- `release.yml`：两次构建 + 校验 + 双产物挂载。
- 提交离线加载 WIP 五处改动（仅这些）。
- 文档四处小更新。
- 新增 build_bundle 测试 + embed vendored-load 测试；全绿。
- 发布流程：合并后打 `v1.1.2` tag → CI 产出并挂载两个 zip。
