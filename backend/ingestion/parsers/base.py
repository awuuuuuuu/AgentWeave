
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

_REQUIRED_METADATA_KEYS = ("source_file", "content_type", "section_path")


@dataclass
class ParsedChunk:
    """parse() 的最小返回单元。

    所有 Parser 输出的 chunk 必须在 metadata 中携带以下字段：
      source_file  : str  — 原始文件名
      content_type : str  — "text" | "table" | "title" | "error"
      section_path : str  — 所属章节路径（无结构的格式填 ""）

    可选字段（有则携带，无则省略）：
      page_number  : int  — PDF/Word 页码（从 1 开始）
      sheet_name   : str  — Excel Sheet 名
    """

    text: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = [k for k in _REQUIRED_METADATA_KEYS if k not in self.metadata]
        if missing:
            raise ValueError(
                f"ParsedChunk.metadata 缺少必填字段: {missing}。"
                f"当前 metadata keys: {list(self.metadata.keys())}"
            )


class BaseParser(ABC):
    """所有文档解析器的抽象基类（策略模式）。

    RAGflow 没有基类，各 Parser 靠命名约定统一接口，导致签名不一致。
    我们用 ABC + abstractmethod 在实例化时就报错，而不是运行到一半才发现。
    """
    # 子类声明自己支持的扩展名，供注册表使用
    supported_extensions: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, source: Union[str, Path, bytes]) -> list[ParsedChunk]:
        """解析文档，返回结构化文本块列表。

        Args:
            source: 文件路径（str/Path）或文件字节流（bytes）

        Returns:
            List[ParsedChunk]
        """

    # ── 供子类复用的辅助方法 ────────────────────────────────────────────────
    def _read_bytes(self, source: Union[str, Path, bytes]) -> bytes:
        if isinstance(source, bytes):
            return source
        return Path(source).read_bytes()
    
    def _source_name(self, source: Union[str, Path, bytes]) -> str:
        if isinstance(source, bytes):
            return "unknown"
        return Path(source).name
    
# ── 注册表 ──────────────────────────────────────────────────────────────────────
# RAGflow 在调用方手动 if/elif 判断文件类型，每次新增格式都要改调用方。
# 我们用字典注册表 + 装饰器，新增 Parser 只需加一个类，pipeline 不用改。

_REGISTRY: dict[str, type[BaseParser]] = {}

def register_parser(cls: type[BaseParser]) -> type[BaseParser]:
    """类装饰器：按 supported_extensions 自动注册到全局注册表。"""
    for ext in cls.supported_extensions:
        _REGISTRY[ext.lower()] = cls
    return cls

def get_parser(file_extension: str) -> BaseParser:
    """按文件扩展名返回对应 Parser 实例。

    未注册的扩展名返回 FallbackParser，不抛异常，避免 pipeline 中断。
    """
    from .fallback_parser import FallbackParser  

    ext = file_extension.lower()
    if ext not in _REGISTRY:
        logger.warning("未注册的文件类型 '%s'，使用 FallbackParser", ext)
        return FallbackParser()
    return _REGISTRY[ext]()