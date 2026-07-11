# 断外网加固 + 长任务可观测性 设计

状态：设计已获用户批准（Gitee 发行明确不做），待实现。

## 背景与目标

两个独立但都很小的补强，源于 2026-07-10 的使用流程审查：

**A. 大陆断外网**：offline 发布包已闭掉大头（vendored 源码 + 清华 PyPI + 内置
embedding ONNX 与 tiktoken BPE；所有云端选项均为国内服务）。残余缺口：
① Python/uv 获取路径没有国内硬指引（uv 自动下 Python 走 GitHub）；
② 用户无法**验证**自己的环境是否真的离线就绪（只能"试试看"）；
③ ollama 后端拉模型走境外 registry、reranker 开启要联网——文档要如实归拢。

**B. 长任务可观测性**：makeitdown 批量转换过程**零进度输出**（pipeline.py 无任何
print），跑几百个扫描件走云端 OCR 时是几十分钟的静默黑箱；agent 的 shell 工具
常有 2–10 分钟超时，前台跑必被掐。有界性已经做对了（每请求 60s 超时、单文件轮询
上限 30 分钟、`--skip-existing` 幂等续跑、`report.json` 是确定性完成契约），
缺的只是**过程可见**与**协议层的长任务模式**。

两部分都不引入第三方依赖、不改架构、不动 rag-retriever 本体。

## Part A · 断外网加固

### A1. `install.py --check-offline`（断网自检，新 flag）

只查不装，人类可读输出（沿用 `_say` 风格），退出码恒 0（与 install.py 降级哲学
一致）。逐项报告 ✓/✗/提示：

1. Python ≥ 3.11；
2. `uv` 在 PATH；
3. `makeitdown --help` 可用；`rag-retriever --help` 可用；
4. `vendor/rag-retriever/rag_retriever/_models/` 下存在 `*.onnx`
   → 「✓ embedding 离线就绪」；否则「✗ 首次建索引将联网下载（HF；可设
   `HF_ENDPOINT=https://hf-mirror.com`，或换用 -offline 发布包）」；
5. `vendor/rag-retriever/rag_retriever/_tiktoken/` 非空
   → 「✓ 分词表离线就绪」；否则「✗ 首次分词将联网拉取（境外 blob，国内常慢）」；
6. 固定提示两条（不检测，如实告知）：reranker（`RAG_RERANK=local`）默认关闭，
   开启需联网下载；ollama 后端拉模型走境外 registry，国内建议 `local`（内置）
   或 `openai`（硅基流动）。

说明：④⑤查的是 bundle 内 vendor 资产（安装即从此本地拷入已装包），是"安装后
是否离线"的忠实代理；自检输出中注明这一点。

### A2. `setup.md` 增补「国内断网 / 内网部署」小节

位置：「两种发布包」小节之后。内容：

- **提前自备 Python 3.11/3.12**：别依赖 uv 自动下载（那走 GitHub 的
  python-build-standalone，国内常断）；uv 本体用
  `pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple` 装。
- 若必须让 uv 管理 Python：设 `UV_PYTHON_INSTALL_MIRROR` 指向国内镜像。
- **内网/涉密部署三件套**（在联网机备好带入内网）：Python 官方安装包 +
  uv wheel（`pip download uv`）+ `-offline` 发布包；进内网后
  `python install.py --ocr local`，装完 `python install.py --check-offline`
  验证离线就绪。
- 归拢两条如实提示：ollama 国内不稳（推荐 local/硅基流动）；reranker 开启联网。
- 一句边界说明：agent 本身（如 Claude）的联网需求超出本项目范围；skill 跨
  agent 可用，配国产 agent 可做到全链路国内。

### A3. `install.py` uv 缺失提示补国内路径

现有提示只有 winget/curl（后者境外）。追加一行：
`或：pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple`。

## Part B · 长任务可观测性

### B1. makeitdown 逐文件进度行（`pipeline.py` + `cli.py`）

`convert_tree` 加关键字参数 `progress: bool = True`（CLI 恒传 True；测试可关）。
`handle` 内用 `time.monotonic()` 计时，返回元组追加 `elapsed`（秒）。在
`as_completed` 主线程消费循环里（天然线程安全、已知 `total = len(files)`）打印
到 **stderr**（`flush=True`，与 cli.py 现有通知同流；stdout 保持干净）：

```
[12/87] ✓ 合同/采购框架.pdf (8.2s)
[13/87] ⚠ 收据.jpg (31.5s)
[14/87] ✗ 损坏.pdf — PDFSyntaxError: ...
[15/87] = 老文件.docx（已最新，跳过）
[16/87] → 演示.wps（需外部转换器，见 report）
```

状态字形：✓ succeeded / ⚠ warned / ✗ failed（附简短错误）/ = skipped_existing /
→ skipped_unsupported。计数 `k` 为完成序号（并发下完成顺序 ≠ 提交顺序，如实）。

### B2. `SKILL.md` 第二步增补「长任务模式」

一段协议文字（agent 无关、可移植）：

> 批量含扫描件/走云端 OCR 时（经验阈值：文件多于 ~20 个或含大量扫描件）：
> **后台运行**并落日志（Claude Code 用 Bash 的后台模式——完成时 harness 会自动
> 唤醒你；其他 agent 用 `nohup makeitdown 原始资料 -o _md > convert.log 2>&1 &`
> 等价形式）→ 期间可 tail 日志按进度行向用户播报 → **以进程退出 + report.json
> 出现为完成信号** → 完成后读 report.json 向用户汇总 succeeded/warned/failed/
> skipped 四类计数与需注意项 → 中断/掉线后 `--skip-existing` 重跑即断点续传。

### B3. makeitdown `README.md` 一句话

「使用」节补一行：转换过程逐文件输出进度到 stderr（`[k/N] ✓ …`），批量大时
建议后台运行并 tail 日志；完成以 `report.json` 为准。

## 测试

- **B1**：`test_pipeline.py` 补——`progress=True` 时 stderr 出现 `[1/1] ✓`
  格式行（capsys/重定向断言）；`progress=False` 时 stderr 无进度行；失败文件
  出 `✗` 行。既有测试直接调 `convert_tree` 者不受破坏（progress 默认 True 只是
  多打 stderr，不改返回值与 report）。
- **A1**：`--check-offline` 为查询模式，逻辑以 `main()` 内联为主；install.py
  现无测试文件，沿现状——以 `--check-offline` 在真实 bundle 结构与裸目录两种
  布局下的手工运行验证为准（E2E 步骤写进 plan），不为其新建测试框架。
- 文档改动（A2/A3/B2/B3）：一致性人工核对（命令拼写与实现一致）。

## 非目标（本次不做）

- **Gitee 发行/镜像（用户明确暂缓）**。
- 不做 PushNotification / Notification hook 的实现（B2 协议文字可顺带提及
  Claude Code 用户可自行配置，不展开）。
- 不给 makeitdown 加 `--no-progress` CLI flag（`progress` 仅作为 `convert_tree`
  的 Python 参数供测试用；CLI 恒开）。
- 不动 rag-retriever 本体；不实现 reranker/ollama 的镜像方案（只文档如实提示）。
- 不做 report.json 增量写入 / heartbeat 文件（进度行 + 退出码 + report 已够）。

## 落地顺序（每段独立可验）

1. **B1 进度行**（pipeline.py/cli.py + 测试）——其余 B 项的地基。
2. **A1 `--check-offline`** + **A3 uv 提示**（install.py）。
3. **文档接线**：A2（setup.md）、B2（SKILL.md）、B3（makeitdown README）。
4. E2E：合成目录跑一次带进度的转换（含一个坏文件）；bundle 布局与裸布局各跑
   一次 `--check-offline`；全套测试回归。
