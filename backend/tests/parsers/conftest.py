"""
pytest fixtures：用 fitz 程序化生成测试 PDF，不依赖外部文件。

每个 fixture 对应一个真实 PDF 场景，返回 bytes。
"""
from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# 调试用：将生成的 PDF 保存到项目根目录，方便肉眼检查
_DEBUG_DIR = Path(__file__).parent.parent.parent.parent  # d:\codes\RAGent
_SAVE_DEBUG_PDFS = False  # ← 改为 True 即可保存


# ── 工具函数 ────────────────────────────────────────────────────────────────────

def _to_bytes(doc: fitz.Document, name: str = "") -> bytes:
    data = doc.tobytes()
    doc.close()
    if _SAVE_DEBUG_PDFS and name:
        (_DEBUG_DIR / f"debug_{name}.pdf").write_bytes(data)
    return data


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture
def single_column_pdf() -> bytes:
    """普通单栏文字 PDF，3 段落。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4

    page.insert_text((72, 100),  "Introduction",                        fontsize=16)
    page.insert_text((72, 140),  "This is the first paragraph of the document. "
                                 "It contains several sentences of narrative text.",
                                 fontsize=11)
    page.insert_text((72, 200),  "Second paragraph continues here with more content. "
                                 "The parser should extract this as a text chunk.",
                                 fontsize=11)
    page.insert_text((72, 260),  "Third paragraph at the bottom of the page. "
                                 "All three paragraphs should appear in the output.",
                                 fontsize=11)
    return _to_bytes(doc, "single_column")


@pytest.fixture
def multi_page_pdf() -> bytes:
    """3 页 PDF，每页内容不同，用于验证 page_number metadata。"""
    doc = fitz.open()
    for i in range(1, 4):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {i} Title", fontsize=16)
        page.insert_text((72, 140), f"This is the content of page {i}. "
                                    f"Each page has unique text for identification.",
                                    fontsize=11)
    return _to_bytes(doc, "multi_page")


@pytest.fixture
def header_footer_pdf() -> bytes:
    """每页都有相同页眉页脚的 PDF（测试过滤效果）。

    注意：fast 策略下 unstructured/pdfminer 不做布局识别，
    Header/Footer 类别需要 hi_res 策略（YOLOX 模型）才能检测。
    本 fixture 主要用于 hi_res 集成测试，fast 模式跳过。
    """
    doc = fitz.open()
    for i in range(1, 3):
        page = doc.new_page(width=595, height=842)
        # 页眉（y=30，接近顶部）
        page.insert_text((72, 30),  "CONFIDENTIAL | Company Name",  fontsize=9)
        # 正文（y=150-400）
        page.insert_text((72, 150), f"Chapter {i}: Main Content",   fontsize=16)
        page.insert_text((72, 190), f"This is the real body text on page {i}. "
                                    f"Only this text should appear in chunks.",
                                    fontsize=11)
        # 页脚（y=810，接近底部）
        page.insert_text((72, 810), f"Page {i} of 2",               fontsize=9)
    return _to_bytes(doc, "header_footer")


@pytest.fixture
def table_text_pdf() -> bytes:
    """包含手动对齐表格文字的 PDF。

    用 fitz 绘制表格边框 + 填入单元格文字。
    fast 策略下 pdfminer 可能把文字识别为 NarrativeText，
    hi_res 策略下 YOLOX 会识别为 Table 类别。
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "Product Inventory", fontsize=16)

    # 绘制表格边框（3 列 4 行）
    col_x = [72, 250, 370, 500]
    row_y = [120, 150, 180, 210, 240]
    for y in row_y:
        page.draw_line((col_x[0], y), (col_x[3], y), color=(0, 0, 0))
    for x in col_x:
        page.draw_line((x, row_y[0]), (x, row_y[-1]), color=(0, 0, 0))

    # 表头
    headers = ["Product", "Quantity", "Price"]
    for j, h in enumerate(headers):
        page.insert_text((col_x[j] + 5, row_y[0] + 20), h, fontsize=10)

    # 数据行
    data = [
        ("Widget A", "100", "$9.99"),
        ("Widget B", "250", "$4.99"),
        ("Widget C", "75",  "$14.99"),
    ]
    for i, row in enumerate(data):
        for j, cell in enumerate(row):
            page.insert_text((col_x[j] + 5, row_y[i + 1] + 20), cell, fontsize=10)

    return _to_bytes(doc, "table_text")


@pytest.fixture
def image_only_pdf() -> bytes:
    """模拟扫描版 PDF：页面上只有图片，无文字层。

    用一个纯色矩形模拟扫描图像，pdfminer 在 fast 模式下
    无法提取任何文字，应触发 fallback 逻辑。
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # 画一个灰色矩形模拟扫描图像（无嵌入文字）
    page.draw_rect(fitz.Rect(50, 50, 545, 792), color=(0.5, 0.5, 0.5), fill=(0.9, 0.9, 0.9))
    return _to_bytes(doc, "image_only")


@pytest.fixture
def scan_pdf() -> bytes:
    """集成测试专用：把文字渲染成位图后嵌入 PDF，模拟真实扫描件。

    fitz fast 模式无法提取文字层（只有图片），hi_res OCR 应能识别出文字。
    """
    # Step 1: 把文字渲染成位图（2× 分辨率，OCR 更准确）
    text_doc = fitz.open()
    text_page = text_doc.new_page(width=595, height=842)
    text_page.insert_text((72, 100), "Scanned document content.", fontsize=16)
    text_page.insert_text((72, 140), "This text lives inside an image.", fontsize=12)
    pix = text_page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img_bytes = pix.tobytes("png")
    text_doc.close()

    # Step 2: 创建只含位图的新 PDF（无文字层）
    scan_doc = fitz.open()
    scan_page = scan_doc.new_page(width=595, height=842)
    scan_page.insert_image(fitz.Rect(0, 0, 595, 842), stream=img_bytes)
    return _to_bytes(scan_doc, "scan")


@pytest.fixture
def encrypted_pdf() -> bytes:
    """密码保护的 PDF（测试容错处理）。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Secret content", fontsize=12)

    buf = io.BytesIO()
    doc.save(
        buf,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="userpass",
        owner_pw="ownerpass",
    )
    doc.close()
    return buf.getvalue()


@pytest.fixture
def attention_paper_pdf() -> bytes:
    """真实学术论文：Attention Is All You Need (NIPS 2017)。

    含双栏布局、数学公式、架构图、性能对比表格、页眉页脚，
    是验证复杂版面解析效果的理想真实文档。
    """
    return (Path(__file__).parent / "fixtures" / "NIPS-2017-attention-is-all-you-need-Paper.pdf").read_bytes()


@pytest.fixture
def corrupted_pdf() -> bytes:
    """损坏的 PDF（非法字节流，测试容错）。"""
    return b"%PDF-1.4\n%%EOF\nthis is not a valid pdf at all!!!"
