"""统一的错误类型:各个后端都抛它,界面只认这一种。"""

from __future__ import annotations


class BackendError(Exception):
    """调用后端失败,message 是可以直接显示给用户的中文说明。"""