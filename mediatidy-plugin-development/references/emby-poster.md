> 来源：MediaTidy 官方 Wiki · Python SDK v2 插件文档
> 抓取日期：2026-11-03
> 原文：https://murongyun8.github.io/MediaTidy-Wiki/plugins/

# Emby 海报预览与上传示例

使用 Python 3.12 和 uv，复用 Pillow 示例锁定的 pillow==11.1.0。在作者环境安装仓库 SDK 的 dev extra，然后在本目录执行 python -m mt_plugin check、python -m mt_plugin pack。示例不需要 Node。
安装后授权 emby.api、具体 Emby 服务器和 storage，无需授权媒体目录。选择服务器并填写实际媒体库或条目 ID，点击“下载并生成预览”。示例下载当前主图，用 Pillow 合成为 1920×1080 JPEG 并发布为宿主临时图片。默认字体用于演示，自定义字体和排版由实际插件决定。
检查预览及显示的目标后，点击“将预览图上传并替换目标主图”。上传固定使用生成预览时保存的服务器、条目 ID 和图片引用，修改表单不会改变已经生成的预览目标。操作会替换该目标的现有主图，请使用测试条目。
图片保留 24 小时，可跨动作和重启预览。过期时重新生成；单实例最多 32 张、64 MiB，多次生成的旧图由宿主过期清理。SDK 抛出超时、取消或响应失败时，先核对 Emby 是否已应用图片，不自动重试。宿主升级提供的是标准接口，第三方海报插件需要按此链路适配。
## 示例源码

- plugin.py
- plugin.json
- pyproject.toml
- uv.lock
