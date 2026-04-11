# RAGent

企业级 Agent + RAG 系统。

---

## 已实现：文档摄入流水线（Step 1）

### PDF 解析器

三档解析策略，适配不同文档类型与成本需求：

| 策略 | 核心技术 | 适用场景 |
|------|---------|---------|
| `fast` | pymupdf (fitz) 纯本地提取 | 普通文字 PDF，速度最快，零外部依赖 |
| `smart` | fitz 优先，按页智能降级 | 通用场景，自动处理混合内容 |
| `hi_res` | 远端 Unstructured API | 复杂版面、扫描件、高精度要求 |

#### 亮点

**智能按页路由**

`smart` 策略逐页分析，只对真正需要的页面调用远端高精度 API，其余页面本地处理。相比整文档走 hi_res，显著降低 API 调用次数和延迟。

```
第 1 页：纯文字 → fitz 本地提取（<1ms）
第 3 页：含图表 → 单页截取 → 远端 hi_res OCR
第 5 页：空页/扫描 → 单页截取 → 远端 hi_res OCR
```

**三重降级触发条件**

```python
# 任一条件满足即触发该页 hi_res fallback
page_has_image   # 光栅图面积超过页面 10%（排除 logo、分隔线等小图）
not page_text    # 全页无文字（扫描页）
_is_garbled(text)  # 不可打印字符比例 > 25%（编码损坏的字体层）
```

- 图片检测同时覆盖内联图片块和 PDF XObject 嵌入图（如论文配图）
- 乱码检测针对扫描版 PDF 经 ghostscript 转换后字体层编码损坏的场景

**降级失败兜底**

远端 API 调用失败时，自动退回到当前页的本地 fitz 提取结果，不中断整体流程，不丢失已解析的其他页面。

**Parser 注册表**

```python
@register_parser
class PdfParser(BaseParser):
    supported_extensions = (".pdf",)
```

新增文件类型只需继承 `BaseParser` 并加装饰器，调用方通过 `get_parser(".docx")` 统一获取，主流程零改动。

---

### 测试覆盖

```
backend/tests/parsers/
├── conftest.py          # 7 个程序化生成的 PDF fixture（fitz 构造，无外部文件依赖）
├── fixtures/
│   └── NIPS-2017-attention-is-all-you-need-Paper.pdf  # 真实论文，验证复杂版面
└── test_pdf_parser.py   # 51 个测试
    ├── 单元测试（41）：mock _call_api，无网络依赖，< 1s
    └── 集成测试（10）：真实 Unstructured API，含论文级复杂文档
```

集成测试覆盖：
- 双栏布局（Attention Is All You Need 论文）
- 扫描件 OCR（文字渲染为位图后嵌入）
- 乱码字体层降级
- 表格结构识别（HTML 输出）
- 页眉页脚过滤

---

## 技术栈（当前）

- **PDF 解析**：pymupdf 1.24+、Unstructured API 0.15+
- **运行时**：Python 3.11+、uv
- **测试**：pytest 8+
