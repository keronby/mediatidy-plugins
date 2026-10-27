# React 示例

后端仍只需 Python。复杂页面可使用 React 和第三方组件。

在 frontend/ 中用 Node 22.12+ 执行 npm ci，然后从项目根执行 python -m mt_plugin pack。
页面导出 ./Page，shared 配置禁止打包第二份 React；MT 提供 @mediatidy/plugin-ui。
本示例启动时检查 React useState 的对象身份，展示主题、语言、尺寸，并在卸载时清理事件订阅。
