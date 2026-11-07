# Exercise Extractor

[English documentation](doc/README_en.md)

从数学教材、习题集和试卷中提取结构化习题。PDF 和图片通过 OCR 转成 Markdown，再按题号切分；已有 Markdown 可直接处理，无需启动 OCR 服务。

## 功能

- **教材**：根据目录的物理 PDF 页码定位习题章节。对没有独立习题目录项的编号小节，优先从 PDF 文字层定位匹配节号的 Exercises 标题；无编号的 Exercises 标题也可依据后续题号归属父级小节。对章末独立的 Problems/Exercises 标题也可向前定位，覆盖整个习题区。没有可靠文字层或标题时保留末尾搜索。正文标题中提及 Problems 不直接当作习题集合。
- **习题集**：按目录或 Markdown 小标题分段提取。
- **试卷**：按题型和题号切分，识别内嵌答案及文末答案区。
- **答案回填**：把答案章节或答案区按题号匹配到习题。
- **结构化输出**：导出每节 Markdown、JSON 和汇总报告；公共 API 返回固定结构的 section JSON。
- **可选审校与图片上传**：可用兼容 OpenAI API 的模型校对 OCR 结果，也可把提取结果中的图片上传到 OSS/MinIO。

## 安装

需要 Python 3.10 或更高版本。安装项目及 `pyproject.toml` 声明的依赖：

```bash
python -m pip install -e .
```

目录识别还依赖 `toc_forge`。请在 `exercise_extractor` 的同级目录克隆并本地安装：

```bash
git clone https://github.com/electroniccc/toc_forge ../toc_forge
python -m pip install ../toc_forge
```

PDF/图片 OCR 还需要可用的 PaddleOCR-VL 模型与本地 vLLM 服务，默认服务地址为 `http://localhost:8080/v1`。目录识别需要 `toc_forge` 及一个兼容 OpenAI API 的模型服务。模型目录可通过 `--model_dir` 或 `EXERCISE_EXTRACTOR_MODEL_DIR` 指定；目录识别服务可通过 `--toc-api-base-url`、`--toc-api-key` 配置，也可使用 `TOC_FORGE_API_BASE_URL`、`TOC_FORGE_API_KEY` 环境变量。

试卷中的成组点状答题线若使 PaddleOCR-VL 输出大量重复字符或重复公式块，OCR 管线会从页面图像定位这些线，遮盖后只重试异常页一次；扫描件不需要文字层。旧缓存中符合该异常特征的页面也会在再次提取时修复。孤立横线、表格线和正常 OCR 页面不触发重试。文字层明确标出的空白页和纯版权尾页不送入 OCR；英文试卷中连续编号的题目表格会还原为普通题号行。`model_dir` 下已有 `PP-DocLayoutV3` 与 `PaddleOCR-VL-1.6` 时，OCR 会直接使用本地模型目录。

## 命令行用法

提取教材：

```bash
python -m exercise_extractor \
  --input textbook.pdf \
  --output output \
  --doc_type textbook
```

提取习题集或试卷时，将 `--doc_type` 改为 `exercise_book` 或 `exampaper`：

```bash
python -m exercise_extractor \
  --input exam.pdf \
  --output output \
  --doc_type exampaper
```

处理已有 Markdown 不需要 OCR、模型目录或目录识别服务：

```bash
python -m exercise_extractor \
  --input exercises.md \
  --output output \
  --doc_type exercise_book
```

CLI 也可以通过 `exercise-extractor` 命令调用。程序不会自动清空输出目录；如果输出目录已存在且非空，请使用新的目录。

## Python API

PDF 或图片通过 `extract_exercise` 逐节产出结果：

```python
from exercise_extractor import extract_exercise

for section in extract_exercise("textbook.pdf", doc_type="textbook"):
    print(section["title"], len(section["exercises"]))
```

Markdown 可直接切分：

```python
from exercise_extractor import extract_exercise_from_markdown

sections = list(extract_exercise_from_markdown(
    "## 习题 1-1\n\n1. 求极限。\n\n2. 证明结论。",
    document_name="示例教材",
    doc_type="textbook",
))
```

两个提取接口都按节返回扁平的公共 section JSON，不暴露内部目录树。PDF/图片提取默认只返回数据；设置 `write_outputs=True` 并提供 `output_dir` 后，还会写出各节 Markdown、JSON 和 `report.json`。

## 提取结果

每道习题包含题号、题目内容、答案和解答过程；有 OCR 页码时还会包含页码信息，公共 JSON 中始终包含 `bbox` 字段。输出目录包括：

- 每节一个 Markdown 文件
- 每节一个 section JSON 文件
- 整次运行的 `report.json`

题干中的答案和解答过程会分别写入 `answer` 与 `solution_steps`。区间题号（如 `4-6`）和多级题号（如 `1.1.7`）均受支持。

## CLI 参数

运行 `python -m exercise_extractor --help` 可查看参数。主要选项如下：

| 参数 | 用途 |
|---|---|
| `--input` | 输入 PDF、图片、Markdown；多张图片以逗号分隔。必填。 |
| `--output` | 输出目录，默认 `output`。目录必须为空或尚不存在。 |
| `--ocr_cache` | OCR 缓存目录，默认 `.ocr_cache`。 |
| `--doc_type` | 文档类型：`textbook`（默认）、`exercise_book`、`exampaper`。 |
| `--model_dir` | PaddleOCR/toc_forge 模型目录；也可设置 `EXERCISE_EXTRACTOR_MODEL_DIR`。 |
| `--hash` | 只打印输入文件 hash 后退出。 |
| `--vl_server_url` | OCR 推理服务地址，默认 `http://localhost:8080/v1`；旧参数名，当前 CLI 使用它配置 `vl_rec_server_url`。 |
| `--vl_rec_backend` | PaddleOCR-VL 识别后端，默认 `vllm-server`。 |
| `--vl_rec_max_concurrency` | OCR 服务最大并发数，默认 `4`。 |
| `--vl_rec_api_model_name` | OCR 服务上的模型名，默认 `PaddleOCR-VL-1.6-0.9B`。 |
| `--vl_rec_api_key`（别名 `--vl-rec-api-key`） | PaddleOCR-VL 推理服务 API key；本地服务通常不需要。 |
| `--vl_model` | 输出后处理使用的模型名；不是 OCR 推理服务的模型名。 |
| `--ocr-device`、`--ocr-engine` | PaddleOCR-VL 与目录 OCR 的设备/推理引擎，例如 `gpu`、`cpu`、`paddle`、`onnxruntime`。 |
| `--toc-vllm-name` | 目录识别模型名，默认 `qwen3.7-flash`。 |
| `--toc-api-base-url`、`--toc-api-key` | 目录识别 OpenAI-compatible 服务地址与密钥；可分别由 `TOC_FORGE_API_BASE_URL` / `ALIYUN_BASE_URL` / `OPENAI_BASE_URL` 和 `TOC_FORGE_API_KEY` / `ALIYUN_API_KEY` / `OPENAI_API_KEY` 提供。 |
| `--review` | 开启基础 LLM 审校。 |
| `--review-base-url`、`--review-api-key`、`--review-model` | 审校服务地址、密钥和模型。密钥默认读取 `OPENAI_API_KEY` 或 `DEEPSEEK_API_KEY`；基础审校默认模型为 `deepseek-flash`，地址默认 `https://api.deepseek.com`。 |
| `--review-with-tools`、`--review-max-rounds` | 额外启用实验性的 SymPy 数学审校；轮数默认 `5`。 |
| `--review-concurrency` | 并发审校小节数，默认 `5`。 |
| `--oss-endpoint`、`--oss-access-key`、`--oss-secret-key`、`--oss-bucket` | 配齐四项后启用 OSS/MinIO 图片上传。 |
| `--oss-region`、`--oss-prefix`、`--oss-use-ssl`、`--oss-public-base-url` | OSS 区域、对象前缀、HTTPS 和公开访问基础 URL。 |

### PaddleOCR 云端推理服务

PaddleOCR-VL 支持使用兼容的托管推理服务，并通过 `vl_rec_api_key` 提供服务 API key。 [PaddleOCR-VL 接入文档](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html) 本项目已支持 CLI 参数 `--vl_rec_api_key`（或 `--vl-rec-api-key`）和 Python API 参数 `vl_rec_api_key`：

```bash
python -m exercise_extractor \
  --input textbook.pdf \
  --output output \
  --doc_type textbook \
  --vl_rec_backend vllm-server \
  --vl_server_url https://your-vlm-provider.example/v1 \
  --vl_rec_api_model_name your-model-name \
  --vl_rec_api_key "$PADDLEOCR_VL_REC_API_KEY"
```

Python API 也可传入 `vl_rec_api_key="..."`。实际可用的服务地址、模型名和支持的 PaddleOCR-VL 版本以服务商文档为准。PaddleOCR 官方托管 API 使用的 `PADDLEOCR_ACCESS_TOKEN` 属于另一套异步 API；本项目没有直接调用那套 API。[PaddleOCR 官方 API 说明](https://www.paddleocr.ai/main/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html)

不要把真实 API key、OSS 密钥或个人服务地址提交到仓库。

## 测试

运行不依赖 OCR 服务的冒烟测试：

```bash
python -m test.smoke_test
```

可通过 `SMOKE_TEST_FULL_PIPELINE_PDF` 和 `SMOKE_TEST_FULL_PIPELINE_DOC_TYPE` 启用使用已有完整缓存的管线测试。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。
