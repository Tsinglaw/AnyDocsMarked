# makeitdown：云端默认 OCR + 显式同意 + MinerU 接入与双 OCR 互校落地

日期：2026-06-29
状态：待确认

## 背景与目标

双 OCR 互校（工作流 C）此前是「框架完整、引擎未接线」的脚手架：`MinerULocal._run_mineru`
抛 `NotImplementedError`、旋转未接线，开启 `--ocr-cross-check` 实际跑不通且会给每个
OCR 文件误标 `quality: suspect`。本设计**真正接通 MinerU**，并按已确认的产品方向把
makeitdown 的 OCR 默认改为**云端优先 + 显式同意**。

经确认的产品决定（不可偏离）：

1. **默认云端**：日常主转换（Paddle）与互校校验方（MinerU）**都默认走云端**。
2. **必须显式同意才上云**：云端**绝不静默上传**。无同意且未显式选本地时，停下并提示用户
   二选一——同意上云，或改用本地。**非交互（agent/批处理）同样不静默上云**。
3. **本地始终可选**：本地是隐私/离线的退路，对两个引擎都保留。
4. **启动醒目提示**：运行时永远打印一条说明——将上传云端、以及如何改用本地。
5. MinerU 云端可现在可靠接入（mineru.net v4，token，可 mock 测试）；MinerU 本地照
   `do_parse` 接、由用户在装好 MinerU 的机器验证。
6. 旋转接线**仍延后**（互校无它也能跑，只对歪扫描件稍弱）。

非目标：旋转纠正的真实接线；改动 rag-retriever；把生成/问答塞进 makeitdown。

## 关键行为：云端同意闸门

新增「云端同意」概念，作用于**任何**云端 OCR 调用（主引擎或校验方）：

- 同意来源：`--cloud-consent` 命令行开关，或环境变量 `MAKEITDOWN_CLOUD_CONSENT=1`。
- **无同意 + 选了云端**（含默认）→ **不运行**，以非零退出码报错，提示：
  > 即将使用云端 OCR，文档会上传至云端服务（Paddle→百度 AI Studio / MinerU→mineru.net）。
  > 如同意上传：设置 token 并加 `--cloud-consent`。
  > 如不希望上传（本机性能足够）：加 `--ocr-engine local`（需安装本地版）。
- **有同意**：打印一条醒目的「正在使用云端，文档将上传」提示后正常运行。
- 选择本地（`--ocr-engine local`）时：与同意无关，照常本地运行，不打印上云提示。

设计要点：同意闸门是**纯函数式的前置校验**，在转换开始前一次性裁决，便于测试；
agent/SKILL 在自己的 setup 流程里事先问用户（现状已如此），CLI 层只做不静默的兜底。

## 引擎与模式解析

### 主引擎（日常转换）
- `--ocr-engine {cloud,local,auto}`，**默认 `cloud`**（原默认 `auto`，本设计改之）。
  - `cloud`：用 Paddle 云端（`CloudOCR`），需 token + 同意。
  - `local`：用 Paddle 本地（`LocalOCR`），需本地版。
  - `auto`：本地已装则本地，否则云端（需 token + 同意），都不满足则报错并给出两条路。
- token：沿用 `PADDLEOCR_AISTUDIO_TOKEN` / `--cloud-token`。

### 互校校验方（MinerU）
- 新增 `--cross-check-mode {cloud,local,auto}`，**默认 `cloud`**（替换当前预留的
  `--cross-check-engine` 死开关——见「废弃」）。
  - `cloud`：`MinerUCloud`（mineru.net v4），需 `MINERU_API_TOKEN` + 同意。
  - `local`：`MinerULocal`（`do_parse`），需本地装 mineru。
  - `auto`：本地已装则本地，否则云端（token+同意），都不满足→**干净跳过**（一条
    `双OCR互校跳过：无可用 MinerU（装本地版或设 MINERU_API_TOKEN）`），不再误标 suspect。

## 架构与改动

### 新增/修改文件（makeitdown/src/makeitdown/）
- `cloud_consent.py`（新）：`require_cloud_consent(args/env) -> None|raises`、
  `cloud_notice() -> str`，纯逻辑、可测。
- `ocr_mineru.py`（改）：
  - `MinerULocal._run_mineru(path)` 真实接 `do_parse`（隔离一处，标注集成点；
    实现时读 `demo/demo.py` 确认确切签名与如何取 markdown + 页数）。
  - 新增 `MinerUCloud`：镜像 `CloudOCR`——`POST mineru.net/api/v4` 提交、轮询、取
    markdown；token 从 `MINERU_API_TOKEN`/参数读，**绝不硬编码**；HTTP 用 httpx，
    可 mock。实现时核对 file-urls/batch → 上传 → 轮询结果的确切端点与 JSON 形态。
- `convert_ocr.py`（改）：`OCRDispatcher` 主引擎默认改 `cloud`；`_resolve_backend`
  的 cloud 分支先过同意闸门；`_make_verifier` 按 `cross_check_mode` 选 local/cloud/auto，
  缓存不变；auto 不可用→返回 None（触发干净跳过）。
- `cli.py`（改）：`--ocr-engine` 默认改 `cloud`；新增 `--cloud-consent`、
  `--cross-check-mode`；启动时按同意闸门打印提示/报错；废弃 `--cross-check-engine`。
- `pipeline.py`（改）：`convert_tree` 透传 `cross_check_mode`、`cloud_consent`；
  同意闸门在批处理开始前裁决一次（不是每文件）。

### 废弃
- `--cross-check-engine`（预留死开关）→ 由 `--cross-check-mode` 取代。

## 错误处理（沿用 makeitdown 铁律）
- 同意缺失 → 立即报错停下、非零退出、给两条路；**不产出任何 .md**（不静默上云）。
- 云端 job 失败 / 网络错 → 单文件计入 `failures`，不中断整批。
- 互校失败/校验方不可用 → 保留主引擎产出 + 一条 warning（绝不丢转换结果）。
- token 缺失（选了云端且已同意）→ 报错提示设 token，不上传空请求。

## 测试（TDD，先写测试，全部离线/mock）
- `test_cloud_consent.py`（新）：有/无同意（开关与环境变量）、本地不触发同意、
  提示文案含「上传/local」关键字。
- `test_ocr_mineru.py`（扩）：`MinerUCloud` 用 mock httpx 断言提交+轮询+取 md；
  token 缺失报错；`MinerULocal._run_mineru` 仍隔离（mock）。
- `test_convert_ocr.py`（扩）：主引擎默认 cloud；cloud 无同意→报错；`--ocr-engine local`
  绕过同意；`cross_check_mode=auto` 无 MinerU→干净跳过（一条跳过 reason，不是 failed）。
- `test_cli.py` / `test_pipeline.py`（扩）：新开关解析；同意闸门在批处理前裁决；
  跳过/失败 reason 进 report+frontmatter；**更新受默认值变更影响的既有用例**。

## 实现期需核对的两处外部事实（隔离为集成点）
1. **mineru.net v4 文件解析的确切流程**：`POST /api/v4/file-urls/batch` 取签名上传 URL →
   PUT 上传 → 轮询取 `full_zip_url`（含 markdown）的端点与 JSON 字段名。
2. **本地 `do_parse` 的确切签名**：import 路径、参数（输入路径、输出目录、backend
   `pipeline`/`vlm`、lang）、产物是写文件还是返回内存——读 `MinerU/demo/demo.py` 确认。

## 受影响文档
- `makeitdown/README.md`：把「本地 vs 云端」对比表更新为「云端默认 + 显式同意 + 本地可选」；
  新增 MinerU（本地安装 / `MINERU_API_TOKEN`）说明；双 OCR 互校改为「可用」表述。
- lawiki `setup.md`：OCR 选择步骤同步「云端默认 + 同意」措辞（如该流程仍引导 OCR 选择）。
