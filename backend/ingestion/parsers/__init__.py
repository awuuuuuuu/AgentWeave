# 导入所有 parser 模块，触发 @register_parser 装饰器完成注册。
from . import csv_parser, excel_parser, fallback_parser, html_parser, markdown_parser, pdf_parser, txt_parser, word_parser  # noqa: F401
