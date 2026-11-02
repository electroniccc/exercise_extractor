# Exercise Extractor

[中文文档](../README.md)

Extract structured exercises from mathematics textbooks, exercise books, and exam papers. PDFs and images are converted to Markdown with OCR and split by question number. Existing Markdown can be processed directly without an OCR service.

## Features

- **Textbooks:** Find exercise sections from the table of contents and optionally search the end of regular sections for exercises without their own contents entry.
- **Exercise books:** Extract sections using the table of contents or Markdown headings.
- **Exam papers:** Split questions by type and number, and handle inline answers and answer sections at the end of a paper.
- **Answer matching:** Match answers from answer sections to exercises by question number.
- **Structured output:** Export Markdown and JSON for each section, plus a run report. The public API returns a fixed section JSON structure.
- **Optional review and image upload:** Review OCR text with an OpenAI-compatible model and upload extracted images to OSS/MinIO.

## Installation

Python 3.10 or newer is required. Install the project and dependencies declared in `pyproject.toml`:

```bash
python -m pip install -e .
```

OCR for PDFs and images also requires a PaddleOCR-VL model and a local vLLM service. The default service URL is `http://localhost:8080/v1`. Table-of-contents recognition requires `toc_forge` and an OpenAI-compatible model service. Set the model directory with `--model_dir` or `EXERCISE_EXTRACTOR_MODEL_DIR`. Configure the table-of-contents service with `--toc-api-base-url` and `--toc-api-key`, or with the `TOC_FORGE_API_BASE_URL` and `TOC_FORGE_API_KEY` environment variables.

> `toc_forge` must be available separately in the active Python environment; it is not listed in this project's `pyproject.toml` dependencies.

## Command-line usage

Extract a textbook:

```bash
python -m exercise_extractor \
  --input textbook.pdf \
  --output output \
  --doc_type textbook
```

For an exercise book or an exam paper, set `--doc_type` to `exercise_book` or `exampaper`:

```bash
python -m exercise_extractor \
  --input exam.pdf \
  --output output \
  --doc_type exampaper
```

Process existing Markdown without OCR, a model directory, or a table-of-contents service:

```bash
python -m exercise_extractor \
  --input exercises.md \
  --output output \
  --doc_type exercise_book
```

The installed `exercise-extractor` command can be used as well. The program does not clear output directories automatically. Choose a new directory if the target already exists and is non-empty.

## Python API

Use `extract_exercise` to yield results section by section from a PDF or image:

```python
from exercise_extractor import extract_exercise

for section in extract_exercise("textbook.pdf", doc_type="textbook"):
    print(section["title"], len(section["exercises"]))
```

Markdown can be split directly:

```python
from exercise_extractor import extract_exercise_from_markdown

sections = list(extract_exercise_from_markdown(
    "## Exercises 1-1\n\n1. Find the limit.\n\n2. Prove the claim.",
    document_name="Sample textbook",
    doc_type="textbook",
))
```

Both extraction APIs return flat public section JSON objects and do not expose the internal section tree. PDF and image extraction returns data only by default. Set `write_outputs=True` and provide `output_dir` to also write Markdown and JSON files for each section, plus `report.json`.

## Results

Each exercise contains its number, question content, answer, and solution steps. Page information is included when available, and the public JSON always contains a `bbox` field. The output directory contains:

- One Markdown file per section
- One section JSON file per section
- `report.json` for the run

Answers and solution steps found after a question are stored separately in `answer` and `solution_steps`. Number ranges (such as `4-6`) and multi-level numbers (such as `1.1.7`) are supported.

## CLI options

Run `python -m exercise_extractor --help` to see the options. The main options are:

| Option | Purpose |
|---|---|
| `--input` | Input PDF, image, or Markdown file. Separate multiple image paths with commas. Required. |
| `--output` | Output directory; defaults to `output`. It must not already contain files. |
| `--ocr_cache` | OCR cache directory; defaults to `.ocr_cache`. |
| `--doc_type` | Document type: `textbook` (default), `exercise_book`, or `exampaper`. |
| `--model_dir` | PaddleOCR/toc_forge model directory; can also be set with `EXERCISE_EXTRACTOR_MODEL_DIR`. |
| `--hash` | Print the input file hash and exit. |
| `--vl_server_url` | OCR inference service URL; defaults to `http://localhost:8080/v1`. This legacy option sets `vl_rec_server_url`. |
| `--vl_rec_backend` | PaddleOCR-VL recognition backend; defaults to `vllm-server`. |
| `--vl_rec_max_concurrency` | Maximum concurrent OCR requests; defaults to `4`. |
| `--vl_rec_api_model_name` | Model name on the OCR service; defaults to `PaddleOCR-VL-1.6-0.9B`. |
| `--vl_model` | Model name for output post-processing; this is not the OCR inference service model. |
| `--ocr-device`, `--ocr-engine` | Device and inference engine for PaddleOCR-VL and table-of-contents OCR, such as `gpu`, `cpu`, `paddle`, or `onnxruntime`. |
| `--toc-vllm-name` | Table-of-contents recognition model; defaults to `qwen3.7-flash`. |
| `--toc-api-base-url`, `--toc-api-key` | OpenAI-compatible table-of-contents service URL and key. They can also be set with `TOC_FORGE_API_BASE_URL` / `ALIYUN_BASE_URL` / `OPENAI_BASE_URL` and `TOC_FORGE_API_KEY` / `ALIYUN_API_KEY` / `OPENAI_API_KEY`, respectively. |
| `--review` | Enable basic LLM review. |
| `--review-base-url`, `--review-api-key`, `--review-model` | Review service URL, key, and model. The key defaults to `OPENAI_API_KEY` or `DEEPSEEK_API_KEY`; the default basic-review model is `deepseek-flash` and the default URL is `https://api.deepseek.com`. |
| `--review-with-tools`, `--review-max-rounds` | Also enable experimental SymPy math review; the round limit defaults to `5`. |
| `--review-concurrency` | Maximum number of sections reviewed concurrently; defaults to `5`. |
| `--oss-endpoint`, `--oss-access-key`, `--oss-secret-key`, `--oss-bucket` | Configure all four to enable OSS/MinIO image uploads. |
| `--oss-region`, `--oss-prefix`, `--oss-use-ssl`, `--oss-public-base-url` | OSS region, object prefix, HTTPS, and public access base URL. |

### PaddleOCR hosted services

PaddleOCR-VL itself supports managed inference endpoints using a backend, service URL, model name, and `vl_rec_api_key`. The official guide includes examples for services such as SiliconFlow and Novita AI. [PaddleOCR-VL integration guide](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html)

However, **Exercise Extractor does not currently pass an OCR service API key**. The CLI has no `--vl_rec_api_key` option, `extract_exercise` has no corresponding argument, and the `PaddleOCRVL` initializer is not given a key. The project can configure the OCR service URL and model name, but cannot currently call an authenticated hosted OCR endpoint directly. `PADDLEOCR_ACCESS_TOKEN` belongs to PaddleOCR's separate hosted API SDK/CLI; this project does not currently call that asynchronous API either. [PaddleOCR official API overview](https://www.paddleocr.ai/main/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html)

Do not commit real API keys, OSS credentials, or private service URLs to the repository.

## Tests

Run the smoke tests that do not require an OCR service:

```bash
python -m test.smoke_test
```

Set `SMOKE_TEST_FULL_PIPELINE_PDF` and `SMOKE_TEST_FULL_PIPELINE_DOC_TYPE` to enable a pipeline test that uses an existing complete cache.

## License

The repository does not currently declare an open-source license. Choose and add an appropriate license file before publishing it publicly.
