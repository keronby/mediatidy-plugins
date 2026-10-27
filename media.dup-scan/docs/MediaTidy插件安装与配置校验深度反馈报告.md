# MediaTidy 插件系统深度排查与作者反馈报告

> **反馈主题**：关于插件安装 400（OPERATION_FAILED）与工作台初始化死锁（插件清单完整性校验失败）的技术排查与系统改进建议  
> **报告时间**：2026-10-27  
> **涉及插件**：媒体查重（`media.dup-scan` / `media.strm-dedup`）、剧集查重（`media.series-dedup`）  
> **测试环境**：MediaTidy 2026 最新版（Docker 容器环境，宿主 Go + Python SDK v2 + MT UI v2）  

---

## 摘要与核心诉求

在近期针对「媒体查重」（原 STRM 重复扫描）和「剧集查重」插件的开发、打包与实际环境验证过程中，我们在宿主机与 Docker 环境下进行了深度的端到端链路跟踪。发现当前系统存在两处**极易导致插件无法安装或陷入死锁**的深层机制瓶颈：

1. **安装/上传阶段（`POST /api/plugins/inspect/upload`）的高灵敏度拦截与错误信息遮蔽**：打包 ZIP 一旦存在任意微小的非致命差异（如临时字节码、声明与代码集合差异），接口在 1~2ms 内直接返回通用的 `400 OPERATION_FAILED`，由于完全不提供错误字段或原因，导致开发者与用户无法判断插件为何无法安装；
2. **启用/保存配置阶段的“清单完整性校验”死锁**：当插件在现场进行参数优化或清单扩展时，Go 宿主会比对安装元数据 `release.json` 与当前 `plugin.json`。一旦两者不完全吻合，系统记录 `[error] 配置校验失败：插件清单完整性校验失败` 并将插件标记为 `status: "error"`。此时用户前端提示 `初始化失败，请检查插件日志、配置或授权后重试`，且点击进入工作台时被拦截为 `NOT_READY`。由于后续任何保存配置或修改授权的 `PUT` 请求均必须先通过该完整性校验，导致插件进入**既无法打开、也无法重配、更无法自愈的永久死锁**。

以下整理出详实的技术复现、逆向追踪证据与切实可行的官方优化建议，供 MT 架构团队参考。

---

## 一、 问题 1：为什么插件“安装不上”？（inspect/upload 拦截机制与黑盒痛点）

### 1.1 业务表象
用户在界面或调用官方 API `POST /api/plugins/inspect/upload` 上传插件 ZIP 包时，频繁遭遇失败：
```json
{
  "code": 400,
  "data": {
    "error": {
      "code": "OPERATION_FAILED",
      "message": "操作未完成，请查看 MT 中对应业务的状态",
      "retryable": false
    }
  },
  "message": "操作未完成，请查看 MT 中对应业务的状态"
}
```
整个请求仅耗时约 1.5ms 即被 Go 宿主反弹，外部既看不到错误日志，也不知道哪个文件不符合规范。

### 1.2 深度排查定位的三大触发场景

经过系统底层分析与对照测试，以下三个极隐蔽的细节都会直接触发 `OPERATION_FAILED`：

#### ① 声明集合与注册集合的强制全等（Strict Handlers Parity）
- **机理**：在 inspect 阶段，宿主会调用隔离 Python 子进程执行 `mt_plugin.worker.load_plugin`。
- **源码断言**：在 SDK 的 `worker.py` 第 104 行中：
  ```python
  if declared != set(handlers):
      raise ValueError(f"Manifest/handler mismatch: declared={declared}, registered={set(handlers)}")
  ```
- **痛点**：若开发者在 `plugin.json` 中声明了一个 action/query（例如准备在下个版本实现的动作，或旧动作重构但未同步清理 json），或者在代码中多注册了一个辅助 query，`load_plugin` 会立即抛出 `ValueError`。Go 宿主捕获到此异常后，未将 mismatch 的差异集合输出到 HTTP 响应或日志，而是统一遮蔽为 `OPERATION_FAILED`。

#### ② 开发残留文件（__pycache__ / .pyc）触发保留环境拦截
- **机理**：在 Linux/Python 环境下运行打包脚本时，Python 解释器会自动在源码目录下生成 `__pycache__/*.pyc` 文件。
- **痛点**：MT Go 宿主在解压检查 zip 目录结构时，对包含保留开发环境路径（如 `__pycache__`、`.venv`、`.git` 等）具有极严格的拦截逻辑。只要打入 zip，整个包会被判定为非法开发文件而直接拒收，同样不返回具体文件路径。

#### ③ pyproject.toml 的 include 通配符与打包脚本不一致
- **机理**：在 `pyproject.toml` 中，若写了 `include = ["core/**", "ui/**"]`，部分精简版 pack 脚本如果仅匹配一级目录名，会导致 `ui/icon.png` 漏打包，服务端直接抛出：
  ```json
  {"error": {"code": "PLUGIN_RESOURCE_MISSING", "message": "插件缺少 ui/icon.png"}}
  ```
  而若 `configSchema` 里的属性名与 `config` 数组的 `key` 存在微小拼写差异（例如 `config` 用了 `localProfileId`，而 `configSchema` 写了 `ruleProfileId`），Go 内部的静态 JSON Schema 验证器直接判定为配置结构非法，同样映射为 `OPERATION_FAILED`。

---

## 二、 问题 2：为什么后台进不去工作台，报“初始化失败”？（完整性校验死锁机理）

### 2.1 业务表象
1. 用户在 MT 网页后台点击「媒体查重」插件，提示：
   `初始化失败，请检查插件日志、配置或授权后重试`；
2. 插件详情页显示状态为 `status: "error"`，尝试打开工作台时，接口 `GET /api/plugins/{id}/ui` 返回 400：
   ```json
   {"code": 400, "data": {"error": {"code": "NOT_READY", "message": "请先配置并启用插件；设置和日志始终可用"}}}
   ```
3. 查询插件错误日志 `GET /api/plugins/{id}/logs`，记录显示为：
   ```text
   [error] 配置校验失败：插件清单完整性校验失败
   ```

### 2.2 深度技术追踪：死锁成因剖析

通过对 MT 插件运行容器内部状态的跟踪，我们复现了该死锁的完整闭环：

```
      [插件初次安装]
            │
            ▼ 生成 release.json 固化当前 manifest 与 hash
            │
      [现场配置调优 / 缺陷修复]
      (开发者优化 plugin.json 配置项，如添加双方案选择字段)
            │
            ▼ 触发 PUT /api/plugins/{id} 保存或启用
            │
      ┌─────────────────────────────────────────────────────────┐
      │ Go 宿主执行完整性断言：                                   │
      │   if manifest_in_file != manifest_in_release:           │
      │       log("[error] 配置校验失败：插件清单完整性校验失败") │
      │       return CONFIG_INVALID                             │
      └─────────────────────────────────────────────────────────┘
            │
            ▼ 插件被置为 status: "error"
            │
      ┌─────────────────────────────────────────────────────────┐
      │ 【致命死锁出现】：                                       │
      │ 1. 用户无法进入工作台（因 status != ready，报 NOT_READY） │
      │ 2. 用户试图重新配置/重新授权点击保存：                   │
      │    宿主强制再次执行完整性校验 -> 再次被拒 -> 持续保持 error │
      │ 3. 插件陷入既无法自愈，也无法在界面上重置的死循环       │
      └─────────────────────────────────────────────────────────┘
```

1. **设计初衷与现实冲突**：MT 设计 `release.json` 与清单完整性校验是为了防止插件在未升级版本的情况下被非法恶意篡改，这在生产安全上是值得肯定的；
2. **死锁痛点**：对于第三方生态开发者或高级用户，在解决现场兼容性（例如修改配置项默认值、调整文案、适配整理方案 ID 等）时，一旦修改了 `plugin.json`，整个插件立即被锁死，页面上所有的“重新保存配置”、“修改授权”都变成无用操作，必须登录宿主机后台手动清空数据库或伪造 hash 才能解开。

---

## 三、 对 MT 架构团队的系统优化建议

为了降低 MT 插件生态的准入门槛，让广大开发者和用户能够顺畅使用第三方业务插件，提出以下建议：

### 建议 1：将 `inspect/upload` 静态检查全面“白盒化”
- **改进方式**：在 `POST /api/plugins/inspect/upload` 返回 400 时，提供清晰的结构化错误明细。例如：
  ```json
  {
    "code": 400,
    "data": {
      "error": {
        "code": "MANIFEST_MISMATCH",
        "message": "插件清单 actions 声明与 Python 注册 handler 不一致",
        "detail": {
          "missingInPython": ["save-rules"],
          "unregistered": []
        }
      }
    }
  }
  ```
- **收益**：避免开发者二分法盲测，能够一秒定位是 json 写漏还是代码未加装饰器。

### 建议 2：为完整性校验增加“安全重载/覆盖更新”入口
- **改进方式**：
  1. 在管理员界面提供「重新验证并信任当前清单」按钮，或者支持通过上传同版本 zip 进行覆盖更新；
  2. 区分**纯代码逻辑（.py）安全校验**与**UI/配置清单（plugin.json）参数声明**：允许在合法管理员签名或调试模式下，自动同步并更新 `release.json`，而不是直接将插件锁死为不可恢复的 `error` 状态。

### 建议 3：规范化生命周期契约提示（`validate_config`）
- **改进方式**：在官方文档与开发脚手架中明确提示：插件必须在 `class Plugin` 中显式实现 `validate_config(self, config: dict, ctx: Context)`，返回 `None` 代表校验通过。若未实现，宿主应做默认 `return None` 宽容保底，避免因缺少该函数而在配置校验阶段引发隐性异常。

### 建议 4：UI v2 AST 组件属性容错与精准路径定位
- **改进方式**：
  1. 对非致命的视觉样式属性（如 `size`、`className`、`style`）采用软忽略策略；
  2. 当出现真正非法的结构性属性时，在控制台和日志中打印组件的 AST 路径（例如 `Page > content[1] > Table.columns[2].cells[0].Button: unknown prop 'size'`），极大降低排障成本。

---

## 四、 附录：以「媒体查重」插件为例的改进实践

在本次适配过程中，「媒体查重」已针对 MT 的架构标准完成了全面工程优化，可作为合规参考：
1. **零第三方依赖**：纯 Python 标准库实现，完全消除环境依赖构建负担；
2. **多路径双方案智能分流**：支持在后台同时配置本地整理方案与分享整理方案，根据路径特征自动适配对应的质量规则与分差门槛，用户无需在工作台繁琐下拉；
3. **原盘 ISO 槽位安全隔离**：读取 `.strm` 首行判定真实扩展名，将 ISO 原盘与 MKV 划分到不同槽位，杜绝误判误删；
4. **256 KiB 存储防溢出**：对结果组进行精细化压缩与上限保护，杜绝大库扫描导致的数据丢失。

---
*本报告由现场研发与排查数据整理生成，欢迎 MT 核心团队就具体技术细节进一步交流探讨。*
