> 来源：MediaTidy 官方 Wiki · Python SDK v2 插件文档
> 抓取日期：2026-11-03
> 原文：https://murongyun8.github.io/MediaTidy-Wiki/plugins/

# 临时图片与预览

插件在 ctx.work_dir 中生成的文件会在动作结束后删除。需要跨动作预览或上传图片时，先发布给宿主；发布只复制图片，不需要用户授权媒体读写目录。
清单声明并获得 storage 权限后，在动作或续接中调用：python
from pathlib import Path
from mt_plugin import ui

output = Path(ctx.work_dir) / "poster.jpg"
# Pillow 或其他库将合成结果写入 output。
artifact = ctx.mt.artifacts.publish(path=str(output))
ctx.mt.storage.set(key="latest", value=artifact)

# get_page 中从查询结果绑定；页面/查询不能发布或删除图片。
image = ui.Image(src=ui.ref("query.latest.data.preview"), alt="海报预览")
# latest 查询返回 {"preview": "file:" + artifact["ref"]}。
```

publish 返回 {ref, contentType, size, expiresAt}。ref 是不透明的实例专属引用，不是路径或公开 URL。Python 页面使用 file:<ref>；React 页面使用 await mt.previewUrl(ref) 取得鉴权资源地址。沿用宿主的短期资源会话，地址不携带主站 JWT 或 Emby API Key。
支持内容验证通过的 PNG、JPEG、WebP，不接受 SVG。每张最多 8 MiB，单边最多 8192 像素、总像素最多 3200 万。每实例最多 32 张、合计 64 MiB；每张从发布时间起保留 24 小时，预览不会延长过期时间。单次运行最多发布 100 次、删除 100 次。
发布后的图片独立于工作目录，跨动作和宿主重启仍可使用。宿主启动时及每小时清理过期图片，也可使用数据管理中的过期清理。清空全部业务存储及卸载插件会删除临时图片，删除部分 KV 键不会删除图片。临时图片不进入业务备份；备份内保存的图片引用恢复后可能失效，应重新生成。python
# 主动释放旧图；不存在的图片返回 False，已删除返回 True。
ctx.mt.artifacts.delete(ref=old_ref)
# 异步处理函数使用 await ctx.amt.artifacts.publish/delete(...)。
```

正在预览或上传的图片暂不能删除，返回 BUSY；稍后再清理。跨实例访问及撤销 storage 后的读取会被拒绝。过期或已删除引用返回 NOT_FOUND，提示重新生成；额度已满返回 QUOTA_EXCEEDED。发布路径必须属于当前动作工作目录，不能借助符号链接读取目录外文件。
Emby 上传可直接使用 body_ref=artifact["ref"]，保持上传内容与预览一致，见 Emby 文件传输。完整的下载、合成、预览和上传流程见 Emby 海报示例。
同为 SDK v2 的旧宿主可能缺少这些方法。可通过 ctx.mt.runtime.capabilities() 检查 artifacts.publish 是否存在，再引导升级宿主；不要根据 SDK 包版本或插件自写提示推断宿主能力。
