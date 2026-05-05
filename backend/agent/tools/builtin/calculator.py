"""
CalculatorTool — 数学计算工具

使用 numexpr 沙箱执行数学表达式，避免 eval() 安全风险。
支持基础四则运算、幂运算、对数、三角函数等 numpy 数学函数。

若 numexpr 未安装则降级为白名单 AST 求值（支持四则运算 + pi/e 常量）。
"""
from __future__ import annotations

import ast
import asyncio
import logging
import operator
from typing import Annotated, Any

from pydantic import BaseModel, Field

from agent.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# 安全 AST 求值支持的运算符白名单
_SAFE_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

# 支持的数学常量（避免 LLM 传入 pi/e 时报错）
_CONSTANTS: dict[str, float] = {
    "pi": 3.141592653589793,
    "e": 2.718281828459045,
}

_MAX_EXPONENT = 1000   # 防止 9**9**9 这类幂塔耗尽 CPU


def _safe_eval(expr: str) -> float:
    """
    安全数学表达式求值（白名单 AST，不执行任意代码）。
    优先使用 numexpr，降级到白名单 AST eval。
    """
    # 优先 numexpr
    try:
        import numexpr  # type: ignore[import]
        result = numexpr.evaluate(expr)
        return float(result)
    except ImportError:
        pass
    except Exception as e:
        raise ValueError(f"numexpr 求值失败: {e}") from e

    # 降级：白名单 AST
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"表达式语法错误: {e}") from e

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f"不支持的常量类型: {type(node.value)}")
        elif isinstance(node, ast.Name):
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            raise ValueError(f"不支持的变量: '{node.id}'（支持的常量: {list(_CONSTANTS)}）")
        elif isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow):
                # 先求指数，防止 9**9**9 这类幂塔耗尽 CPU
                exp = _eval(node.right)
                if abs(exp) > _MAX_EXPONENT:
                    raise ValueError(f"指数过大（>{_MAX_EXPONENT}），拒绝执行")
                return operator.pow(_eval(node.left), exp)
            op_fn = _SAFE_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
            return op_fn(_eval(node.left), _eval(node.right))
        elif isinstance(node, ast.UnaryOp):
            op_fn = _SAFE_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
            return op_fn(_eval(node.operand))
        else:
            raise ValueError(f"不支持的表达式节点: {type(node).__name__}")

    return _eval(tree)


class CalculatorArgs(BaseModel):
    expression: Annotated[
        str,
        Field(
            description=(
                "数学表达式字符串，例如 '2 ** 10'、'(3 + 4) * 2'、'1024 / 8'。"
                "支持 +、-、*、/、**、//、% 运算符和括号。"
            )
        ),
    ]


class CalculatorTool(BaseTool):
    """安全执行数学表达式计算"""

    name = "calculator"
    description = (
        "计算数学表达式的结果。"
        "支持四则运算、幂运算、取模等基础数学操作。"
        "输入必须是合法的数学表达式字符串。"
    )
    cacheable = True
    timeout = 5     # 计算应极快完成

    @classmethod
    def get_args_schema(cls) -> type[BaseModel]:
        return CalculatorArgs

    async def _arun(self, expression: str) -> ToolResult:
        # 基础安全检查：拒绝明显的代码注入
        banned = ["import", "exec", "eval", "open", "__", "os.", "sys."]
        for token in banned:
            if token in expression:
                return ToolResult(
                    tool_name=self.name,
                    content=f"表达式包含不允许的关键字: '{token}'",
                    is_error=True,
                )

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, _safe_eval, expression)
        except (ValueError, ZeroDivisionError) as e:
            return ToolResult(
                tool_name=self.name,
                content=f"计算错误: {e}",
                is_error=True,
            )
        except Exception as e:
            logger.exception("CalculatorTool: unexpected error")
            return ToolResult(
                tool_name=self.name,
                content=f"计算异常: {e}",
                is_error=True,
            )

        # 整数结果去掉小数点
        display = int(result) if result == int(result) else result
        return ToolResult(
            tool_name=self.name,
            content=f"{expression} = {display}",
            metadata={"expression": expression, "result": result},
        )
