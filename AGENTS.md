# Exercise Extractor

从数学教材 PDF 中自动提取习题的工具。核心流程：识别目录（toc_forge）→ 定位习题页 → OCR（PaddleOCR-VL）转 markdown → 按题号切分习题 → 导出 markdown / JSON。

## 模块结构

| 文件 | 职责 |
|---|---|
| `exercise_extractor/main.py` | 主入口与 CLI（argparse）：解析参数后走 pipeline，安装后可用 `python -m exercise_extractor` 调用 |
| `exercise_extractor/pipeline.py` | 可复用提取管线 `run_pipeline`（CLI 与对外 API 共用）：目录识别 → OCR → 拼页 → 切分 → 答案匹配 → 导出 |
| `exercise_extractor/api.py` | 对外公共 API `extract_exercise(source, source_type="pdf", doc_type, ...)`：输入 PDF 路径，返回习题 JSON（与 exercises.json 同结构）；OCR 引擎参数（vl_rec_backend/vl_rec_server_url/vl_rec_max_concurrency/vl_rec_api_model_name）全部可配；预留 source_type 扩展 image/markdown |
| `exercise_extractor/__main__.py` | `python -m exercise_extractor` 调试入口（等价于仓库根 `python main.py`） |
| `exercise_extractor/ocr.py` | 目录识别（build_sections，toc_forge + 阿里云 vLLM）与 OCR（ocr_pages，PaddleOCR-VL） |
| `exercise_extractor/extraction.py` | 提取链：目录树 → 习题区域（build_section_tree）、待 OCR 页码、拼页、规则切分（extract_exercises）；后续 LLM 检查/审核在此插入 |
| `exercise_extractor/exercise_book.py` | 习题集专用流程：有目录按目录层级整节提取，无目录整本 OCR 后按小标题层级切块；题号切分/图注/表格与 textbook 共用 |
| `exercise_extractor/exampaper.py` | 试卷专用流程：不走目录识别，整卷全量 OCR；按题型小节（选择题/填空题/简答题）切分，处理内嵌【答案】/【解】与文末答案区回填 |
| `exercise_extractor/parse_md_exercises.py` | 核心切分器：把 markdown 文本按题号拆成结构化习题（与输入来源无关） |
| `exercise_extractor/toc_detect.py` | 纯目录树判断：习题章节识别、前后置内容跳过、是否启用节尾搜索 |
| `exercise_extractor/answers.py` | 习题答案章节：按小标题切分答案、与习题节点匹配（含题数校验） |
| `exercise_extractor/html_tables.py` | HTML 表格工具：检测严格闭合的 HTML 表格（detect_html_table）、转 markdown 表格（html_table_to_markdown） |
| `exercise_extractor/postprocess.py` | 后处理：统计报告（collect_report）、导出 md/JSON（write_outputs）、控制台摘要（print_summary） |
| `exercise_extractor/utils.py` | 基础工具：页面转图像、文件 SHA256 哈希、markdown 块公式修复 |
| `main.py`（仓库根） | 兼容入口 shim：`python main.py ...` 等价于 `python -m exercise_extractor ...` |
| `pyproject.toml` | pip 打包配置（setuptools），console script `exercise-extractor` |
| `test/smoke_test/` | 冒烟测试模块（81 项）：`common.py` 公共设施（断言计数/CLI 子进程/场景加载）、`scenarios.py` 场景测试与注册表、`unit.py` 单元断言、`runner.py` 入口；运行 `python -m test.smoke_test`。场景化测试文本统一放 `test_scenarios/*.txt`，每个场景一个 txt |
| `test_scenarios/` | 场景化测试文本：每个场景一个 txt（近真题/旧真题/解析/文末答案/表格答案区/伪造缓存页/习题集等），smoke_test 依次扫描并按文件名分发 |

## 主流程（main.py）

1. **build_sections**（ocr.py）：`toc_forge.bookmark_pdf` 做目录识别（阿里云 vLLM 视觉 API，模型 `qwen3.7-flash`，API key 取环境变量 `ALIYUN_API_KEY`），得到目录树后经 `build_section_tree` 筛出习题章节，每节带 `exercise_page_range: [start, end)`。
2. **ocr_pages**（ocr.py）：对未缓存的习题页转图像，交给 PaddleOCR-VL（llama.cpp server，默认 `http://localhost:8080/v1`）转 markdown，按 PDF 哈希分目录缓存（`page_N/page.md`）；已有缓存自动跳过。
3. **combine_section_pages**（extraction.py）：把每节若干页 markdown 拼接，页间插入 `<!-- page: N -->` 标记供页码溯源。
4. **extract_exercises**（extraction.py）：切分、按小节过滤、处理隐式节尾习题、题号重排为 1,2,3…（原题号保留在 `original_number`）。
5. **answers.extract_and_match**：对 `is_answers` 节点拼页、按小标题切块、与习题节点匹配并校验题数，结果写入 `node["answer_sections"]`。
6. **write_outputs**（postprocess.py）：导出每节一个 markdown（题号用 `*N.*` 行内斜体）、`exercises.json`（完整数据）、`report.json`（统计报告）。

exampaper 分支不经过第 1/5 步：`build_sections` 直接返回整卷节点（不跑 toc_forge），`ocr_pages` 全量 OCR，`exampaper.extract_exercises` 按题型小节切分并处理答案。

## 数据结构

### Exercise（parse_md_exercises.py）

- `number` / `content` 必有；`separator`、`section`、`pages`、`original_number` 视情况存在。
- `number` 可能是 `"__preamble__"`（区间内无编号题时的整段内容占位）。
- 支持区间题号（`"4-6"`）与多级题号（`"1.1.7"`）。
- `answer` / `solution_steps`：每个习题都带这两个属性（无内容时为空字符串）。`answer` 来自题干后面的答案段（行首 `答：`/`答:`/`[答]`/`（答）` 起到题目结束）；`solution_steps` 来自行首 `解：`/`解:`/`[解]`/`（解）` 起的解答过程段（裸"解"必须有冒号，避免误判"解析/解决/解释"；带括号的写法无歧义）。两者都从 `content` 中切出单独存放，markdown 输出时以 `解：`/`答：` 单独成段写回。
- `question_type` / `section_key` / `type_index`：仅 exampaper 习题带。`question_type` 为标准英文枚举：`single_choice`（单选题，普通“选择题”默认单选）/ `multiple_choice`（多选题）/ `fill_in_blank`（填空题）/ `short_answer`（简答题，含解答/计算/应用题及无题型词的旧式小节兜底）/ `proof`（证明题）；`section_key` 为归一化题型小节键（保留中文，如 `一、选择题`/`二`），用于答案区回填；`type_index` 为小节在卷内顺序。

### SectionNode（main.py）

- `title`、`has_exercise`、`children`；习题节点含 `exercise_page_range: [start, end)`；隐式节尾习题节点带 `implicit: True`；切分后写入 `exercise_content` 与 `exercises`。

### Report（main.py）

- 总习题数 + 每节摘要（页码范围、缓存页数、习题数、题号列表），用于验证与排查缺页/range 问题。

## 关键设计决策

- **结构驱动、不针对特定教材**：切分靠「行首 `N + 分隔符` + 题号单调递增」判新题，用 `(?!\d)` 防止把 `2.5` 等小数误判为题号；允许 OCR 噪声前缀（`*^$` 等）。
- **目录习题关键词**：`toc_detect.EXERCISE_TITLE_KEYWORDS` 覆盖 习题/练习/作业/思考题/例题（含补充例题）等中英文关键词——思考题、例题/补充例题作为显式习题目录项参与提取（如周衍柏《理论力学教程》各章"思考题""补充例题"）。
- **多级题号允许 2 段**：支持 "1.1"、"5.8" 这类「章.题」编号（周世勋《量子力学教程》等教材）。2 段数字在正文里也可能是节引用（如 "4.9 节的定理"），由后续的小节归属过滤 / 连续性校验 / 节尾匹配规则兜底。
- **显式习题节点从自身标题行截断**：目录页码可能落在上一节正文上（页面上半是例题、下半才是 "## 习题"），提取前先 `truncate_to_section_heading` 截到本节点自己的标题行，避免正文混入第一题。
- **题号行被 OCR 渲染成标题时还原**：OCR 偶尔把题号行写成 markdown 标题（如 "### 7.5 设氢原子的状态是"）。只有「多级题号 + 习题动词（求/设/证明/解/判断/计算…）开头」的标题行会被还原为普通题号行，避免误伤真正的章节标题（"### 4.5 狄拉克符号"）。
- **上下文判定的误转标题还原**：`split_exercises` 遇到标题行时，若行首能解析出题号（`数字.`/`数字-数字.`/`数字.数字`）、与当前题号序列同模式且连续、且标题之后还有同模式连续题号，则认定是 OCR 把题号误转成了标题（如高数"### 3. 回答下列问题："），去掉 `#` 还原为普通题号行。此规则不依赖动词表，可兜住单数字题号（如 `### 2. δ 函数为偶函数`），但要求后续题号连续，避免误伤 `### 2. 全同粒子体系` 这类合法小节标题。
- **题干/解答过程/答案分离**：习题内容里行首 `答：`/`[答]` 起的段落切到 `answer`、行首 `解：`/`解.`/`解．`/`解 `（空格）/`[解]` 及**独立成段的 `证明.`/`证明：`/`证.`/`证：`**切到 `solution_steps`（`content` 只留题干）。**"证明"多标记规则**：内容里有多个行首"证明/证"标记时，题干中靠前的"证明：…"是要证的结论，解答以**最后一个**"证明/证"开头，从它切起；只有一个标记且是题干第一个非空行时不切（纯证明题题干，如 `1.1.1 证明： (a)...`）；只有一个标记但不是第一个非空行时切（超甜风格：题尾独立解答）。每个习题都保证带这两个属性、无内容为空字符串；markdown 输出时以 `解：`/`答：` 单独成段写回。负向断言排除"答案/答题/答 案/证明题"等字样。
- **配图按图注回插**：切分前先把内容里的 `<img>` 行抽走并按**图片下方的图注**归类（支持 `图 2-12`、`补充例题1.3题图` 等格式，默认图注在图片下方，同一图注下的多张子图归到一起；夹在子图之间的短标签行如 `(a)(b)(c)(d)` 也随图组走），图注位置留占位标记（`<!-- figure: 键 -->`）；切分完成后**两遍回插**：第一遍按题干里的"图N-M"引用插到引用行之后（引用优先），第二遍把未被引用的图按占位标记位置兜底插回（如理论力学"补充例题1.3题图"在 [解] 流程中间、题干不引用图号）。占位标记无论是否附图都不留在最终内容里。
- **HTML 表格转 markdown 表格**：提取完习题后，对习题与答案内容里的 HTML 表格做检测（内部标签严格闭合才转换）并替换成 markdown 风格表格，否则单元格内的 LaTeX（`$...$`）在多数预览器下无法渲染。模块：`html_tables.py`（`detect_html_table` / `html_table_to_markdown` / `replace_html_tables`）。
- **节尾搜索（textbook 模式）**：`should_use_tail_search` 统计目录中普通小节后有习题目录项的比例；若大多数小节没有目录项，则对每个小节末尾取 3 页建隐式习题节点。提取后要求题号基本连续、剔除 `__preamble__` 和归属为其他已知小节的内容，防止把正文/下一节内容当习题。本次运行是否启用节尾搜索会以 `tail_search_used` 字段写入每个节点（exercises.json 可查；隐式节点另带 `implicit: True`）。
- **习题区间含边界页 + 边界标题截断**：隐式节点和显式习题节点的页码范围都含下一目录节点的起始页（不少教材把习题印到下一节标题所在页顶部，如高数习题1-1 的 17/18 题在"第二节"标题页、梁昆淼的节尾习题）；提取时找到边界节点的标题行（`boundary_title`）即截断其后的内容，避免把下一节的内容误当本节习题。**兜底**：目录标题与页内标题写法可能不一致（如目录 "2 连续函数" vs 页内 "## 第2章 连续函数"），标题匹配失败时按 `boundary_page`（节点上记录）定位，用 `truncate_before_page_heading` 截到该页第一个标题行之前。
- **隐式节点丢弃 leading**：节尾区间刻意从上一节正文/上一题解答的中段开始，`split_exercises(keep_leading=False)` 丢弃第一道题前的正文（如上一道补充例题的解答尾巴），不拼进第一题；显式节点保持默认拼接行为。
- **小结/总结/参考书节点跳过**：目录树里标题含「小结/总结/参考书（含参考书目/主要参考书/参考文献）」的节点完全跳过，不建节尾习题节点——小结常是连续数字编号的短段落（参考系定义、开普勒定律等），参考书是书目列表，符合题号特征但不是习题（如周衍柏《理论力学教程》各章小结、林宗涵《热力学与统计物理学习题解答》的"主要参考书"）。
- **数字题必须匹配到小节结尾**：`split_exercises` 末尾对每小节做后置校验（`check_section_end`）——若最后一题的正文里出现「定理/定义/引理/命题/推论」编号环境（如 `定理 5.1`），说明 `1. 2. 3.` 只是正文中间的分类叙述，整组丢弃。**该校验只用于隐式节尾节点（正文中挖题）**；显式习题节点、习题集、答案块关闭（`check_section_end=False`）——这些是已知习题区域，解答里引用"定理 N"会误伤整组。刻意不含「例」（如 `例 1.24` 会误伤 δ 函数性质这类小节尾内容，这类情况留给后续 LLM 检查）。
- **多级目录深度**：页码边界逐级下传（本节点结束 = 下一兄弟节点；最后一个子节点上溯取祖先的 next），支持任意层级。
- **试卷（exampaper）整卷提取**：不走目录识别，整卷 OCR（`exercise_page_range=[1, N+1)`）。`split_type_sections` 识别“一、选择题/二、填空题/三、解答题”题型小节，含“单选题/多选题/单项选择题/多项选择题/不定项选择题”等变体（兼容早年“二、（本题满分8分）”这类无题型词标题，以及“二、【解】”这类解析文档标题）。题型分类：多选/多项选择/不定项 → `multiple_choice`，普通选择 → `single_choice`，填空 → `fill_in_blank`，标题题型词是“证明题” → `proof`（“解答题…证明过程或演算步骤”里的“证明”不算），其余 → `short_answer`。答案区标题只在已出现至少一个题型小节后生效，且“题型词+答案/解析”紧邻才算答案区小节，避免“请将答案写在答题纸…”这类说明文字误触发；无标题答案区（春季卷“题号/答案”表格直接开头）通过 HTML 表格头识别。选择题/填空题用按行切分器（`_split_simple_numbered_exercises`，避免 “11. 3” 被多级题号正则吃掉；`）`/`)` 不当题号分隔符，避免把 (1)(2) 子问误拆；只有该节第一个编号行是括号编号的旧式试卷才整体转换 (1)(2)）。简答题保留 (1)(2) 子问、只把 >=10 的括号题号（如 (17)）转成普通题号；`N.（本题…` 无空格也认（点号后 `(?!\d)` 防 “17.2024年” 年份粘连）。题型小节内统一去掉 markdown 标题前缀（OCR 会把题号行渲染成 `### 18.`/`## (17)`，也会把跨页标题拆成残行如 `## 算步骤`）。解析文档的【答案】/【解】/【解析】/【详解】/【分析】/【点睛】拆到 answer/solution_steps（标记可能独占一行，也可能跟在答案后同处一行）；文末答案区（“参考答案”或“题号/答案”表格）按小节键+题号回填，没有小节标题时整段按题号全卷匹配。
- **习题答案章节**：目录树里标题含「习题答案/参考答案/答案与提示/习题解答」的节点（`is_answers_title`）标记为 `is_answers`，页码范围 = `[起始页, 下一节点首页)`；没有 boundary 时默认持续到文档最后一页（`build_section_tree` 需要 `doc_page_count`）。`answers.py` 把答案页按小标题切块（markdown 标题行 / 行首 `§1.1` 标签）；**行首能解析出题号的标题行（如 "### 6 —7. 略"）不切块**，原样交给 `split_exercises` 的上下文规则判定还原，避免答案块被从中间切断。每块用 `split_exercises` 切答案并重编号；匹配策略：章级答案块（标题含"章"）优先显式习题节点、隐式节尾节点兜底，按章号+目录顺序一一对应，`count_match` 按**展开后的有效题数**（区间答案按覆盖题号逐个计数）与习题数比较。匹配后做**逐题填充**：答案块按题号（`original_number` 优先）对齐到每道习题的 `answer` 字段，**区间答案（"6-7"）展开填给 6 和 7**，已有内联答案的不覆盖，未命中的保持空字符串；答案节点保留 `answer_sections` 作为数据源，习题节点不挂整块答案。
- **标题重置题号序列**：`split_exercises` 遇到标题行会重置 `prev_key`，否则"第二篇"这类独立编号段落（如答案章节从 1 重新开始）会被上一篇的题号吞掉。
- **缓存体系**：OCR 结果按 PDF 哈希缓存（`.ocr_cache/exercise_extractor/{hash}/markdown/page_N/page.md`）；`--hash` 参数只打印哈希不跑流程。
- **输出目录保护**：程序不自动删除输出目录；发现 `--output` 目录非空即提示用户并退出（需自行清空或换新目录）；`--output` 指向已存在文件时同样报错退出，不留给 `os.makedirs` 抛 traceback。
- **doc_type**：`textbook` 启用节尾搜索；`exercise_book` 走 `exercise_book.py` 专用流程（有目录：目录树叶子小节整体作为习题区域、不做小节归属过滤，起始页用 `truncate_from_page_heading` 兜底——目录标题可能比页内标题多前缀，如目录"习题 1-2 数列的极限" vs 页内"## 数列的极限"；无目录：整本 OCR 后按小标题层级切块再按题号提取）；`exampaper` 走 `exampaper.py` 专用流程（整卷 OCR + 题型小节切分，跳过 toc_forge 与通用答案章节匹配）。
- **OCR 批大小**：PaddleOCR-VL genai 客户端默认 batch_size=8192（整批页面合成一批，`_doc_vlm_genai_collect_responses` 等整批完成才输出，导致结果到最后才一次性落盘）；`create_ocr_pipeline` 会把 `vl_rec_model.batch_sampler.batch_size` 强制改为 8，配合并发 8 实现按 8 个一批实时输出。
- **批量 OCR 缺页兜底**：批量 `predict_iter` 正常返回但结果数少于请求页数时（静默截断，不抛异常），批量循环结束后按 `page.md` 是否存在逐个补齐缺失页；仍失败的单页走原有 FAILED 标记流程，不静默丢失。

## 运行方式

```bash
python main.py --input temp/xxx.pdf --output output --doc_type textbook
```

## 安装与对外接口

```bash
# 可编辑安装（仓库根目录；依赖已装好的情况下可用 --no-deps）
.venv/bin/python3 -m pip install -e . --no-deps

# CLI：安装后两种写法等价
python -m exercise_extractor --input xxx.pdf --output output --doc_type textbook
exercise-extractor --input xxx.pdf --output output --doc_type exampaper

# 公共 API：输入 PDF，返回习题 JSON（与 exercises.json 同结构）
from exercise_extractor import extract_exercise
data = extract_exercise(
    "xxx.pdf",
    source_type="pdf",
    doc_type="exercise_book",
    output_dir=None,
    model_dir="/path/to/paddle/official_models",
    vl_rec_backend="vllm-server",
    vl_rec_server_url="http://localhost:8080/v1",
    vl_rec_max_concurrency=4,
    vl_rec_api_model_name="PaddleOCR-VL-1.6-0.9B",
)
```

`extract_exercise` 默认只返回数据不落盘；`write_outputs=True` 且给 `output_dir`
时会额外写出 md / exercises.json / report.json。OCR 需要本地 llama.cpp
server（默认 `http://localhost:8080/v1`），`toc_forge` 为本地依赖，
安装时若 pip 找不到可先 `--no-deps` 再用已有环境。`model_dir` 不内置机器
路径：显式传入，或设置环境变量 `EXERCISE_EXTRACTOR_MODEL_DIR`。旧接口名
`extract_pdf` 保留为别名（后续移除）；`vl_server_url`/`vl_model` 为 OCR
参数的旧别名（后续移除）。`source_type` 当前仅支持 `"pdf"`，后续扩展
图片/markdown 时在 `api.SUPPORTED_SOURCE_TYPES` 与分发逻辑中增加。

依赖：`pymupdf`、`toc_forge`、`paddleocr`（PaddleOCRVL）、`cv2`、`numpy`、`Pillow`。目录识别需要 `ALIYUN_API_KEY` 环境变量；OCR 需要本地 llama.cpp server（默认 8080）。

## 测试

```bash
# 冒烟测试（不依赖 OCR 服务，仅“超甜”管线需要本机缓存）
python -m test.smoke_test

# 可选：启用“全缓存管线”测试（不绑定机器路径，用环境变量指定 PDF）
SMOKE_TEST_FULL_PIPELINE_PDF=/path/to/fully-cached.pdf python -m test.smoke_test
# 可选指定文档类型（默认 exercise_book）
SMOKE_TEST_FULL_PIPELINE_DOC_TYPE=exampaper SMOKE_TEST_FULL_PIPELINE_PDF=... python -m test.smoke_test
```

测试拆分为 `test/smoke_test/` 下的模块：场景测试（`scenarios.py`）按
`test_scenarios/*.txt` 依次加载分发，单元断言在 `unit.py`，公共设施在
`common.py`，入口在 `runner.py`。测试不绑定当前机器：CLI 子进程用
`sys.executable`，全缓存管线默认跳过（环境变量启用），场景文本全部自包含。

CLI 的 OCR 引擎参数：`--vl_server_url`（旧名，兼容）、`--vl_rec_backend`、
`--vl_rec_max_concurrency`、`--vl_rec_api_model_name`；
`--vl_model` 只控制输出后处理（含 PaddleOCR 时启用块公式修复）。

## 当前状态

- `.ocr_cache/` 有 5 个不同 PDF 的处理缓存，其中一个已缓存 244 页 markdown。
- `temp/` 存放待处理教材 PDF（概率论、线性代数、高数上下册、复变函数等）。
- git 仓库尚无提交。
