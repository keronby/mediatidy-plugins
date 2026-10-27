# Pillow 示例

使用 Python 3.12、uv 0.6.7，按 `pyproject.toml` 准备 Pillow 和 SDK 作者环境，再执行 `python -m mt_plugin pack`。

安装后授权一个可读写的本地或 FUSE 图片目录。选择一张图片，填写目标授权根 ID、相对路径与最长边像素后运行。Pillow 从工作文件读取并缩放，宿主接管输出、排队写入，内部 collect 续接保存预览引用。

在宿主任务中确认最终状态，再刷新图片预览。目标已存在时不会隐式覆盖；当前云盘路由不支持二进制操作会明确报错。示例不需要 Node。
