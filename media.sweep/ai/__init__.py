# -*- coding: utf-8 -*-
"""AI 识别分区：在 sha1 比对未命中的文件上做二次识别，再交 MT 整理。"""
from .identify import AiIdentifyMixin, validate_prompt

__all__ = ["AiIdentifyMixin", "validate_prompt"]
