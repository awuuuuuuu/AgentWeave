# 文档解析器（Parsers）

所有解析器的设计目标：**结构保留优先于文字完整性**。
RAG 的检索质量高度依赖 chunk 的语义边界——一个正确切开的章节远胜于把全文塞进一个 chunk。

---

## 架构：注册表 + 策略模式

```python
@register_parser
class PDFParser(BaseParser):
    supported_extensions = (".pdf",)
```

`get_parser(".pdf")` 统一入口，新增格式只需加一个类，主流程零改动。
未注册的扩展名自动路由到 `FallbackParser`，pipeline 不中断。

所有 parser 在 `__init__.py` 集中导入，保证装饰器在首次 `import parsers` 时完成注册。

---

## PDF 解析器（`pdf_parser.py`）

### 设计思想

PDF 内部是"绝对坐标上的字符流"，没有语义标签。唯一能区分"这一页好不好"的信号，是提取出来的文字质量本身。

### 三档策略

| 策略 | 核心技术 | 适用场景 |
|------|---------|---------|
| `fast` | pymupdf 纯本地提取 | 普通文字 PDF，零外部依赖 |
| `smart` | fitz 优先，按页智能降级 | 通用场景，自动处理混合内容 |
| `hi_res` | 远端 Unstructured API | 复杂版面、扫描件、高精度要求 |

### 智能按页路由（`smart` 策略核心）

逐页分析，只对真正需要的页面调用远端Unstructured API，其余页面本地处理：

```
第 1 页：纯文字 → fitz 本地提取（<1ms）
第 3 页：含图表 → 单页截取 → 远端 hi_res OCR
第 5 页：空页/扫描 → 单页截取 → 远端 hi_res OCR
```

三重降级触发条件（任一满足即走远端）：

```python
page_has_image    # 光栅图面积超过页面 10%（排除 logo 等小图）
not page_text     # 全页无文字（扫描页）
_is_garbled(text) # 不可打印字符比例 > 25%（字体层编码损坏）
```

远端调用失败时自动退回本地 fitz 结果，不丢页、不中断。

### section_path

PDF 无语义结构，`section_path` 留空 `""`。后续可通过字体大小 + 加粗做启发式标题检测，但属优化项。

---

## Word 解析器（`word_parser.py`）

### 设计思想

Word 文档的结构信息藏在 `styleId` 里，而非用户看到的样式名称。
用样式名（"标题 1"、"Heading 1"）匹配会被多语言 Office 击穿；用 `styleId`（`Heading1`）则语言无关。

### 核心机制

- **标题识别**：正则匹配 `styleId` 中的 `Heading(\d+)`，兼容所有语言版本的 Office
- **section_path**：维护 `{level: heading_text}` 栈，每个 chunk 记录所属章节路径（如 `第一章 > 1.1 节`）
- **表格降维**：首行加粗 → 键值对（`Product: Widget; Price: $9.99`）；无加粗表头 → 竖线拼接。比保留 HTML 更适合 LLM 理解

### 为什么表格不保留 HTML

Word 表格用 python-docx 提取后本就没有 HTML 结构，强行重建反而引入噪声。键值对格式让 LLM 直接读懂行列关系，无需解析标签。

---

## HTML 解析器（`html_parser.py`）

### 设计思想

网页的噪声密度远高于 Word/PDF：导航栏、广告、脚本、注释……RAG 不需要这些。
清洗策略是"白名单思维"：只提取有意义的块级内容，其余静默丢弃。

### 核心机制

- **html5lib 后端**：容错最好，自动补全残缺标签，企业爬取的页面经常残缺
- **噪声标签静默丢弃**：`nav / aside / form / script / style / header / footer` 等一律 `decompose()`
- **叶子块过滤**：`div > p` 这种嵌套结构只提取叶子 `<p>`，避免文字重复
- **表格保留 HTML 字符串**：HTML 表格结构丰富（colspan、rowspan），保留原始 HTML 供后续专用渲染器处理，同时附 `plain_text` 供检索用
- **section_path**：与 Word 相同逻辑，从 `h1-h6` 栈构建

### chardet 编码检测

企业内网页面编码混乱（GBK、Big5、Latin-1），chardet 自动检测，三级 fallback 保证不崩溃。

---

## TXT 解析器（`txt_parser.py`）

### 设计思想

纯文本没有任何结构信号，唯一可用的边界是**空行**。
按连续空行切块，每块作为一个 chunk，保留块内换行（行尾空白清理即可）。

不压平换行的原因：TXT 文件里的换行往往有语义——列表的每一项、代码的每一行都是独立信息，压成一行会让 LLM 误读。

`section_path` 留空，无结构信息可提取。

---

## Markdown 解析器（`markdown_parser.py`）

### 设计思想

Markdown 的结构信号比 TXT 丰富得多（`#` 标题），但有一个经典陷阱：
代码块里的 注释`# comment` 不是标题。必须用状态机追踪代码块边界，才能正确区分。

### 核心机制

- **YAML frontmatter 提取**：`---` 块内的 `key: value` 写入每个 chunk 的 metadata，支持文档级元数据（author、date、tags 等）透传到向量库
- **标题状态机**：维护 `heading_stack` 构建 `section_path`，逻辑与 Word/HTML 对齐
- **代码块状态机**：` ``` ` / `~~~` 触发 `in_code_block` 标志，块内内容原样保留，不做标题匹配
- **换行保留**：`"\n".join(lines)` 而非压平，Markdown 列表、代码块的缩进结构完整保留

### frontmatter 解析的取舍

只做简单 `key: value` 解析，不引入 PyYAML 依赖。复杂嵌套结构（列表值、多行值）直接跳过——对 RAG 来说，`title` / `author` / `date` 这类扁平字段已覆盖 90% 的使用场景。

---

## CSV 解析器（`csv_parser.py`）

### 设计思想

CSV 的核心难点不是读取，而是**不知道表头在哪里**。企业导出的 CSV 经常在真正的表头之前附带几行元信息（公司名、报告期、制表人），直接取第一行作为表头会把元信息当列名。

### 核心机制

- **启发式表头检测**：扫描前 10 行，优先选第一个非空列数 ≥ 2 的行；若后续某行非空列数超出当前最优 +1 则覆盖（参考 Dify）
- **Preamble 提取**：表头之前的信息行解析为 `key: value` 字典，写入每个 chunk 的 metadata。支持三种格式：
  - 单列冒号格式：`"公司名称：XX集团"` → `{"公司名称": "XX集团"}`
  - 双列格式：`["报告期", "2024Q1"]` → `{"报告期": "2024Q1"}`
  - 多列并排：`["制表人", "张三", "审核人", "李四"]` → 成对提取两个 kv
- **分隔符嗅探**：`csv.Sniffer()` 自动检测逗号/分号/制表符
- **UTF-8 BOM 处理**：Excel 导出的 CSV 经常带 BOM，优先剥离再解码
- **每行一个 chunk**：格式 `字段: 值; 字段: 值`，空值字段跳过

---

## Excel 解析器（`excel_parser.py`）

### 设计思想

Excel 比 CSV 多两个结构维度：**多 Sheet** 和**合并单元格**。合并单元格的子单元格值为 None，如果不填充，LLM 拿到的每一行都缺少合并列的信息。

### 核心机制

- **Sheet 名作为 section_path**：每个 Sheet 独立处理，Sheet 名透传到每个 chunk 的 `section_path` 和 `sheet_name` 字段，检索时可按 Sheet 过滤
- **合并单元格填充**：预扫描 `sheet.merged_cells.ranges`，构建 `(row, col) → value` 填充表，所有子单元格取主单元格的值。例：部门列合并 10 行"销售部"，填充后每行都能看到"销售部"
- **与 CsvParser 共用逻辑**：表头检测（`_detect_header`）、Preamble 提取（`_extract_preamble`）、行转文本（`_row_to_text`）完全复用，保证行为一致


---

## FallbackParser（`fallback_parser.py`）

### 设计思想

pipeline 遇到未知格式时不应崩溃，也不应产生垃圾数据。两个核心原则：

1. **二进制嗅探优先**：检查前 8 KB 是否含 null byte（`\x00`）。含则判定为二进制，直接返回 `[]`，不尝试解码。防止 `.zip`、`.exe`、`.png` 等被 latin-1 解码成几 MB 乱码灌入向量库。

2. **不注册到注册表**：`supported_extensions = ()`，调用方必须显式实例化或通过 `get_parser` 的兜底逻辑获取，不会抢占任何已知格式。

---

## 结构感知切分（与 Splitter 层的分工）

Parser 层已完成所有的**结构感知工作**，Splitter 层只处理 token 长度约束。两层职责如下：

| 层 | 职责 | 如何感知结构 |
|----|------|-------------|
| **Parser** | 按文档原生结构切块 | Word/HTML/Markdown 按标题层级；PDF 按页；Excel 按 Sheet |
| **Splitter** | 把超长 chunk 切到 token 上限内 | 不感知结构，只看 `content_type` 和 token 数 |

**关键设计**：Parser 输出的每个 chunk 已经在一个语义单元内（同一章节、同一页、同一 Sheet），Splitter 无法跨越这些边界——不是因为 Splitter 知道结构，而是 Parser 已经保证了边界。

**`content_type` 路由**：Splitter 通过 `content_type` 字段做差异化处理：

- `title`：标题极短，切分无意义；恒定透传
- `table`：token 数 ≤ 2048 时透传（跨行切断表格会让 LLM 看到半张表）；超过 2048 token 强制切分，防止 `TokenLimitExceeded`

**`section_path` 的用途**：`section_path` 是给 **LLM 和用户**看的元信息，不是向量库过滤字段。

- ✅ 正确用途：拼入 prompt 告知 LLM 结果来源（"此内容来自《Q3报告》第三章 3.2节"）；前端引用展示
- ❌ 不适合：作为 Milvus scalar filter——不同文档的标题文字完全不同，跨文档精确匹配没有意义
- 如需按文档范围过滤，使用 `source_file` 字段

**强制 metadata 规范**：所有 Parser 输出的 chunk 必须携带三个字段，在 `ParsedChunk.__post_init__` 中校验：

```python
_REQUIRED_METADATA_KEYS = ("source_file", "content_type", "section_path")
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_file` | `str` | 原始文件名，用于溯源 |
| `content_type` | `str` | `"text"` / `"table"` / `"title"` / `"error"` |
| `section_path` | `str` | 章节路径，无结构格式填 `""` |

---

## 测试覆盖

```
backend/tests/parsers/
├── conftest.py                 # PDF fixture（fitz 程序化构造，无外部文件依赖）
├── fixtures/
│   └── *.pdf                   # 真实文档，验证复杂版面
├── test_pdf_parser.py          # 51 个测试（41 单元 + 10 集成）
├── test_word_parser.py
├── test_html_parser.py
├── test_txt_parser.py
├── test_markdown_parser.py
├── test_csv_parser.py          # 含分隔符嗅探、preamble 提取、编码检测
├── test_excel_parser.py        # 含多 Sheet、合并单元格、preamble 提取
└── test_fallback_parser.py     # 含二进制嗅探、注册表隔离测试
```
