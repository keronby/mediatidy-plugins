# 快速开始：第一个 Python 插件

准备 Python **3.12** 和 uv **0.6.7**。默认 Python 页面不需要 Node；React 页面另需 Node **22.12+**。先从[开发资源](downloads.md)取得完整 Skill，或使用主仓库的 `plugin-sdk/`。

## 选择模板

| 示例 | 主仓库位置 | Skill 包中的位置 |
|---|---|---|
| 目录扫描与任务续接 | `plugin-sdk/starter/` | `assets/plugin-sdk/starter/` |
| Pillow 图片处理 | `plugin-sdk/examples/pillow/` | `assets/plugin-sdk/examples/pillow/` |
| React 交互页面 | `plugin-sdk/examples/react/` | `assets/plugin-sdk/examples/react/` |
| Table 行内操作与分键分页 | `plugin-sdk/examples/storage-table/` | `assets/plugin-sdk/examples/storage-table/` |

把所需模板复制到自己的工作目录，修改 `plugin.json` 的 `id/name/author/description`。保留 `entry: "plugin.py:Plugin"`、`sdkVersion: "2"` 和 UI v2 声明。React 模板的本地前端依赖需要一起复制或重新指向公开前端 SDK，见 [UI](ui.md)。

目录模板是小规模教学示例，将报告放在单个 KV 值中。扩展到大量文件时应改成分键存储与分页查询，遵守[工作流容量](workflow-capacity.md)，不要把模板当成无上限目录索引器。

## 准备作者环境

在复制后的插件目录执行（PowerShell）：

```powershell
uv sync --python 3.12
# 替换为本机主仓库的 plugin-sdk，或解压 Skill 的 references/sdk
$sdkPath = 'C:/path/to/mediatidy-plugin-development/references/sdk'
uv pip install --python .venv/Scripts/python.exe "$sdkPath[dev]"
uv run --no-sync python -m mt_plugin check
uv run --no-sync python -m mt_plugin pack
```

Linux/macOS 对应命令：

```bash
uv sync --python 3.12
sdk_path=/path/to/mediatidy-plugin-development/references/sdk
uv pip install --python .venv/bin/python "${sdk_path}[dev]"
uv run --no-sync python -m mt_plugin pack
```

SDK 只安装在作者环境，**不要**用 `uv add mt-plugin` 把它写入插件运行依赖或锁文件。`--no-sync` 防止 uv 移除额外安装的作者工具。`check` 和 `pack` 都会更新锁文件、导入作者入口并运行 mypy，只用于你信任的源码。

## 编写入口

以下展示处理函数形状；`inspect`、`summary` 还需要在清单的 `actions`、`queries` 中声明，配套清单见[清单与参数](manifest.md)。

```python
from mt_plugin import Plugin as BasePlugin, Context, action, query, ui

class Plugin(BasePlugin):
    def on_init(self, ctx: Context):
        self.count = 0
        ctx.mt.log(level="info", message="插件已初始化")

    @action("inspect")
    def inspect(self, input: dict, ctx: Context):
        self.count += 1
        return {"count": self.count}

    @query("summary")
    def summary(self, input: dict, ctx: Context):
        return {"count": self.count}

    def get_page(self, ctx: Context):
        return ui.page(ui.Button(
            label="检查", onClick=[{"type": "invoke", "actionId": "inspect"}]
        ))
```

对象会复用，进程重启会丢失内存状态。需要持久化时声明并使用 `storage`。业务和读取通道可能同时访问对象，涉及共享状态时由作者处理一致性。文件选择由宿主放入 `ctx.files`，不是任意绝对路径参数。

## 打包与验证

发布包根目录至少包含 `plugin.json`、`plugin.py`、`pyproject.toml`、`uv.lock`。`pack` 输出 `dist/<插件ID>-<版本>.zip`。其他 Python 模块、提示词和资源应加入 `[tool.mt-plugin].include`；目录被递归收集。

使用 MT「上传 ZIP」读取清单，检查授权后安装。依赖准备完成后仍需配置、启用，再执行小规模测试并查看最终任务状态。也可提交完整可安装内容到公开 GitHub/Gitee 仓库；React 必须携带预构建 `ui/`。开发 Skill ZIP 不是插件安装包。

开发机检查不能代替宿主检查、目标平台依赖安装或实际页面验证，完整步骤见[验收](validation.md)。
