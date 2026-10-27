# -*- coding: utf-8 -*-
"""dup-scan 插件的纯逻辑层。

这一层不碰宿主 SDK（不 import mt_plugin），只吃「文件名 + 目录相对路径」，
所以能在本地目录上直接跑、能单测、能在容器外验证。
宿主 SDK 的调用全部放在 plugin.py 里。

模块：
  nameinfo  从文件名解析规格（分辨率/编码/HDR/码率/声道/季集）
  rules     把 MT 的质量评分规则翻译成本项目形式
  scoring   按规则打分
  workscan  在单个作品目录内找重复并判胜负
"""
