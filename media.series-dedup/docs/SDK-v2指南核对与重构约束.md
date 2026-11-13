# SDK v2 指南核对与媒体查重重构约束

核对日期：2026-11-12。对象：media.series-dedup；业务对齐 mt-console 的“媒体查重”，不是 STRM 清洗。

## 依据与版本

- 官方入口：https://murongyun8.github.io/MediaTidy-Wiki/plugins/
- 官方完整包：https://murongyun8.github.io/MediaTidy-Wiki/downloads/mediatidy-plugin-development-2.0.0.zip
- Wiki 下载页注明包更新于 2026-11-06，但 SDK 版本仍为 2.0.0。不能仅凭版本号判断契约一致。
- 参数/上下文以同包 `references/sdk/mt_plugin/methods.json` 为准；UI 以同包 `ui-registry.json` 为准；最终还须核对目标宿主支持情况。
- 本次完整包下载在 /tmp/mt-sdk-current/skill.zip，解压在 /tmp/mt-sdk-current/unpacked/mediatidy-plugin-development/。临时目录不是项目依赖。
- 实际对比本地旧 SDK：新增 artifacts.delete/publish、emby.request、emby.servers.list、ffprobe.cacheExists；文件、任务、整理、质量评分、runtime 的部分 schema 有变化。新版包含 DirectoryPicker，旧副本不包含。

## 必须遵守的工程约束

1. **路径和授权**：files.roots 返回 id/name/storageType/read/write/delete，无绝对路径。DirectoryPicker 保存 `{rootId,path}`，根自身 path 为 `.`；不从名称、绝对路径或前缀猜根。根授权和能力授权分别检查。SDK 不是原生 Python 安全沙箱，但不能用直接文件访问绕过 SDK 拒绝。
2. **整理**：organize.manual.run 逐文件传 fileRef/profileId/tmdbId/mediaType/idempotencyKey，可带 season/episode；不传 sourceDir/destDir。草稿最终由宿主确认，不存在 worker organize.submit/confirm。
3. **任务**：queued 不等于完成。保存真实 task ref；tasks.wait 前保存状态，登记后立即 return，包括 finally 内也不能继续调用 SDK。续接同时需要 @continuation 和清单 kind:continuation。完成后核对最终状态。
4. **幂等**：提交前保存稳定幂等键与请求状态；不同请求不能复用同键。submitting/outcome_unknown 先核对原任务，不自动重新生成键重试。取消不承诺回滚已完成操作。
5. **容量**：读取 runtime.info().limits.maxWorkflowFiles；缺失按 100，目前上限 500。列表/任务明细每页 100，底层扫描上限 10000；files.list 每运行 500 次。达到容量或深度限制必须表示部分结果，不得宣称完整扫描。用户明确启动下一批，不用无限续接规避预算。
6. **存储**：默认每实例 32 MiB/10000键，单值 256 KiB、batch 1 MiB；按实际 JSON UTF-8 字节计算。found 区分缺失与 null/false/空值。on_init 只补缺失。配置是宿主配置，业务结果用 KV，跨回调不依赖 work_dir。
7. **结果分片**：scans/<runId>/summary、groups/<序号>、大组 versions/<组序号>/<分片>；activeScan 与 currentScan 分开。先 running，全部成功后原子切换 done/currentScan；失败保留旧成功结果和部分结果，不静默截断。分页使用 nextCursor，不自行计算偏移；编辑使用 revision/expectedRevision。
8. **通道**：validate_config 纯校验；page/query 只读；生命周期只调用允许能力。同步用 ctx.mt，异步用 await ctx.amt。顶层 Python kwargs 为 snake_case，嵌套 JSON 保持 camelCase。
9. **UI**：state/config/input 独立；config 是草稿须显式保存。invoke 只等待入队，紧接 query 可能读旧数据。Button.loading 不代表任务完成。页面最多 256 KiB/500组件/深度12/Table12列。DirectoryPicker/loading/size 必须确认宿主支持。
10. **诊断**：保留 SDKError code/path/retryable，不将失败转换为空列表/零结果。采集同调用 runtime.info/capabilities/resources/files.roots；granted 不替代资源/版本校验。不输出私有 STRM 链接、凭据。tmdb.detail 无强制刷新参数，不能声称实时强制刷新；media.parse 不返回最终分类，不能假称等价控制台 parse-test(full)。

## 对当前实现的影响 / 未完成项

- 现有按配置/根名称推导绝对路径的旧兼容逻辑需要替换，不能作为通用根定位方式。
- 本地扫描诊断必须分别显示访问目录数、识别作品数、候选数、读取错误和截断；其他用户零结果的根因尚未实证，不能断言没有挂载。
- 现有固定数量截断的 share/local/mixed 状态需迁移为分片游标方案。
- 提交/删除需统一 SDK 引用、真实任务状态、稳定幂等和未知结果保护。
- 混合链接评分仍是计划层，未证明全链路对齐；不执行真实删除。
- 用户既定业务语义仍保留：STRM 内容只做链接分类，不把其中目标地址直接作为整理/删除源；源目录映射要改为用户明确选择的授权根与相对路径，不猜根。

## 验证与交付门槛

Python 3.12、uv 0.6.7，作者虚拟环境安装官方 SDK[dev]，但不加入插件运行依赖/锁文件。执行：

```sh
uv run --no-sync python -m mt_plugin check
uv run --no-sync python -m mt_plugin pack
```

用 ContractTransport 检查输入输出/通道/授权及注入失败；它不能证明真实文件版本、整理、生命周期和并发正确。之后在隔离宿主验证安装、UI、授权、BUSY、续接、部分失败、取消、未知提交和更新保留数据。

本次仅文档和契约学习核对，不代表官方 check/pack 或真实宿主验收通过；不因此部署、推送、改生产安装目录或执行云端删除。

## 当前源码复核记录

以下是源码检查结果，不代表重构已经完成：

| 位置 | 已核实行为 | 后续处理 |
| --- | --- | --- |
| plugin.json | v1.1.12 已声明 files.delete/tmdb.read；未声明 continuation；当前没有 rules.read | 不应误报缺少删除权限；评分接入时按实际调用补权限 |
| plugin.py:60 `_storage_set_safe` | 列表超预算自动截短；异常返回 False | 改为明确错误及完整分片，保留结构化异常 |
| plugin.py:77 `_storage_get_safe` | 没有正确按 found 区分缺失；异常返回默认值 | 失败不得伪装空状态 |
| plugin.py:89 `_save_state_split` | share/local 限120、mixed限60，调用方不检查写入结果 | 替换为完整分片与成功提交指针 |
| plugin.py:214 `_known_roots_from_config` | 使用根名称和配置路径推导映射 | 替换为明确保存的 rootId/相对路径 |
| plugin.py `Plugin.on_init` | 每次重写保护组 | 仅补真正缺失值，读取失败不得清空 |
| plugin.py:1516 `Plugin.scan` | 同步扫描本身并不违规：动作默认预算30分钟；后台宿主任务仍需续接 | 区分原生计算和宿主异步任务；正确检查预算/取消/部分结果 |
| series_core/submitter.py:111 `submit_tasks` | 已经复用逐文件 SDK 提交，未提供 SDK 时拒绝 HTTP 回退 | 不能套用早期版本“批量仍走 HTTP”的诊断；补任务汇总和续接 |
| plugin.py `_execute_delete_entry` | 当前动作路径调用 sdk_delete_dir | 原生删除函数存在不等于 UI 动作调用它 |
| series_core/deleter.py:82 `delete_media_entry` | 仍保留原生 shutil/os 删除，入口仅导入，当前动作使用另一 SDK 函数 | 删除遗留实现或明确封闭入口；审查 SDK 任务完成判定 |

学习结论：不能凭旧版本报告推断当前行为，也不能把“同步耗时”一概判为 SDK 违规。真正需要等待续接的是宿主返回 task ref 的异步业务；本地计算另受动作预算、内存和协作取消约束。

## 当前实现验证记录（2026-11-12，开发中）

已实现：严格 KV found 语义与错误传播；原生删除入口停用；删除任务引用、等待续接和确认后精确移除；跨流程删除路径锁（CAS 创建，不自动释放）；无截断的字节分片快照与最后发布指针；结果列表分页（20 行且每分类 35000 字节预算）；旧存储兼容读取；扫描错误状态通过快照发布。

验证命令及边界：
- 本机 Python 3.11 `python3 -m unittest discover -s tests`：37 项通过，包括旧 core 测试，不代表所有测试都覆盖当前 series_core。
- 隔离镜像 Python 3.12.12、uv 0.6.7、mypy 1.19.1：临时副本 `python -m mt_plugin check` 通过；此前临时开发包 pack 通过。最新路径锁变更后重新 check 通过，未重新 pack。
- 官方 CLI 原版在 uv 版本门禁失败：`AssertionError: Use uv==0.6.7`。实际 `uv --version` 为 `uv 0.6.7`（没有后续空格），原版要求 startswith("uv 0.6.7 ")。仅临时 SDK 副本将其改为按空白分词比较前两项；其余检查原样，未修改下载的原 SDK。不能称为原版 CLI 无修改通过。
- 尚未验证真实宿主安装、浏览器 UI、云盘任务、真实删除或整理。开发包不是发布包，版本号仍 1.1.12，禁止作为升级发布。

剩余阻断项：目录仍有旧版绝对路径与授权根推导，需迁移 DirectoryPicker/rootId-relative path；整理任务续接未完成；快照保留/孤立分片回收未实现，反复写入会消耗配额；分页尚无快照代际绑定，扫描切换时需重置游标与选择；单条超限仍显式拒绝，需拆明细；保护组仍单键；读取分片和任务调用预算需联合核算；删除目录数量限制不能代替递归文件数量限制；路径锁需显式恢复流程；SDK 故障结构化传播与完整 ContractTransport 测试尚需覆盖；混合评分尚未接入动作。以上未闭环前不得生产部署或宣称控制台全量对齐。


## MTC 兼容实现（2026-11-12）

按用户要求：SDK 缺少的能力采用 MTC 实现，不将缺失能力伪装为成功。
新增 `series_core/mtc.py`，迁移 MTC 的缓存查询→唯一 cacheId 刷新→回读，以及 parse-test(full) 最终分类接口。HTTP 经 `ctx.mt.http.request`，而非直接 urllib 绕过宿主 HTTP 授权。

配置方式：宿主配置 JSON 增加 `mtcBaseUrl`（例如 `http://你的MT地址:2019`），宿主密钥填写 `mtcToken`（有效 MT Bearer Token）；授予 `http.request` 对该地址的访问和 `secrets.use`。地址和密钥不进入业务 KV，密钥不进入 UI/日志，不猜密码、不自动登录。未配置地址时分类核验保持关闭，强制刷新明确拒绝，而非用 SDK 查询冒充刷新。

- “刷新 TMDB”动作使用实际刷新 POST，严格区分 movie/tv，同 ID 不混用；单次最多15作品（每作品3 HTTP），完成后提示重新扫描，不直接宣称页面已重评。
- 扫描在配置兼容地址时核验最终分类，每次最多40个作品目录；超出明确要求缩小分区。分类返回必须精确匹配目录、TMDB ID 和媒体类型，失败保持 unknown，绝不多数票猜测。整理/删除地址仍来自作品目录及配置映射，STRM 内容仅用于分类。
- 网络超时/HTTP错误不自动重放 POST。刷新可能已生效，应在 MT 核对后再操作。Token 失效需用户更新，不使用历史默认密码。
- 当前仅离线/契约检查，未验证真实 MT HTTP 响应或网络授权；没有执行真实刷新、整理或删除。新增权限需升级时重新授权。原有路径映射、整理续接、快照回收等剩余工作仍未完成。

## 用户修正：本地 STRM 扫描独立于 MT（2026-11-12）

此节覆盖此前关于扫描使用 DirectoryPicker/rootBindings 的设计。用户明确要求：查 STRM 用本地，不跟随 MT 文件接口限制。

- 扫描、保存扫描路径统一恢复本地绝对路径字符串 `scanPath`，持久化字段 `localScanPath`；兼容读取旧 `rootPath`。旧 picker 对象不猜测转换成本地路径。
- 扫描不要求 rootBindings、不调用 files.roots/stat/list，不受宿主文件工作流的100/500或整理器150文件限制；不自动猜 /vol1 等前缀。
- 从主扫描移除逐目录 MTC 分类核验及40目录阻断；扫描仍可查询 TMDB 元数据，不代表完全离线，元数据接口的配额不等于本地扫描数量上限。
- SDK 整理/删除继续遵守实际接口的授权和任务约束；显式挂载绑定仅用于这些操作的路径适配，不作为本地扫描前置条件。评分预览目前仍保留其自身授权检查，与主扫描分开。
- 已测：601个本地作品目录全部识别并保留；扫描动作在空本地样本下无宿主文件/配置/密钥依赖。66项离线测试通过；Python3.12.12 check/mypy/pack通过（仍使用仅修正uv版本判断的临时SDK副本）。未部署、未真实云盘操作。

## 1.2.0 本机部署验证（2026-11-12）

用户批准升级部署，plugin.json/pyproject.toml/uv.lock 版本同步为1.2.0。升级前备份安装详情、旧包和SQLite一致性副本至 backups/pre-1.2.0-20261112-213436/（含私有配置，禁止分发）。通过 inspect/upload + POST /api/plugins/install 原位更新，实例ID保留，沿用旧资源授权；未新增HTTP目标或秘密凭据。安装后恢复 enabled，状态ready。

实际验证：GET UI成功；save-config动作done且保留既有方案和映射；本地 /strm/云盘影视/电影 扫描done，识别26108作品目录、遍历26127目录，未识别0、scan_errors为空，当前结果分享0/本地0/混合2组/保护15。只代表本机当前规则结果，不证明其他用户路径问题全部解决。页面API验证未代替浏览器视觉验收。未执行整理/删除/真实MTC刷新，未推送GitHub。开发SDK检查仍采用已记录的uv判断兼容补丁。

## 1.2.1 纠正 MTC 含义（2026-11-12）

用户澄清 MTC 指 mt-console 源码实现，不是新增远程服务。此节覆盖此前“MTC 兼容层”和地址/Token 设置说明；这些配置不再适用。

- 删除错误添加的 series_core/mtc.py、其远程分类适配器及桥接测试；移除 mtcBaseUrl、mtcToken、http.request、secrets.use。不要求用户配置另一套服务或凭据。
- 保留直接移植的 mt-console 本地解析、命名匹配、质量评分逻辑；本地 STRM 扫描不变。
- refresh-tmdb 保留动作 ID 兼容性，标签改为“重新查询 TMDB 元数据（非强制刷新）”，仅调用宿主 SDK 查询，明确返回 force_refresh=false；最终分类接口缺口仍待实现，不声称已由纯本地逻辑替代。
- 61项离线测试通过（移除远程桥接测试并新增无远程依赖回归）；Python3.12.12 SDK check/mypy/pack通过，仍使用已记录的临时uv判断修正。发布包 dist/media.series-dedup-1.2.1.zip。
- 部署前保存安装详情和1.2.0包到 backups/pre-1.2.1-*/。升级首次因安装请求未保留 capability 授权导致 storage 初始化失败；恢复升级前全部原授权，并在停用/启用两个 PUT 中均提交完整 config+grants 后恢复 ready。PUT 缺失 grants 会清空授权，后续部署工具必须避免。未新增 rules.read 授权。
- 最终真实宿主版本1.2.1、状态ready，UI和status query均200，实例ID保持不变，旧扫描快照可读；未触发整理、删除、强制刷新或新扫描，未推送GitHub。

## 1.2.2 代码与流程复核（2026-11-12）

发现并修复：

1. 整理器接收根对象列表，但插件传入 rootId→path 字典，会在收集文件阶段报错。现在显式转换为对象列表，并有入口适配回归测试。
2. 删除入口直接信任 UI 路径/链接类型；现在必须匹配当前快照中唯一、未禁用、同链接类型的条目，否则副作用前拒绝。
3. 删除 continuation 将缺失 failed 当0。现在仅接受整型 failed=0 且status=done；整理 continuation 同样拒绝缺失及布尔值。
4. 删除完成只更新表格、未更新 full_groups，会让批量整理重读旧目录。现在一次快照发布同步清理精确路径和完整分组，不按 basename 删除。
5. 删除目录数上限500会超出每项锁+记录写入的预算；改为宿主上限与150取小值（仅操作预算，不限制本地扫描）。递归删除的宿主文件数量限制仍由宿主最终执行，无法以目录数证明递归文件数。
6. 云盘路径转换仍猜测 /strm、/CloudNAS、/115open；移除固定挂载前缀与目录名去重，现仅按明确配置的本地前缀→云盘前缀做边界匹配替换，未命中拒绝。

校核：66项离线测试通过、compileall和diff-check通过、Python3.12.12 SDK check/mypy/pack通过（临时SDK中uv版本判断兼容修正同前）。1.2.2已原位部署，升级前保存安装信息/1.2.1包；本次完整传入grants，并在重启时保留config/grants，最终ready、UI200、status query200、实例ID不变。未执行真实整理/删除。

流程仍有明确边界：本地扫描→元数据比较→完整快照/分页已离线及此前实扫验证；整理/删除需显式rootBindings，现有安装尚未配置，因此不能声称真实整理/删除端到端已通过。混合质量为预览动作，尚未接自动删除；最终分类核验缺口未补；任务未知结果和完成后锁均保留，暂缺解锁恢复入口。新增rules.read未替用户授予，评分需按实际配置授权。以上均不能以check/pack通过代替实际业务验证。

## mt-console 媒体查重流程对照及 1.3.2（2026-11-12）

用户批准增加 rules.read。保留完整旧 config/grants/instance，部署1.3.0并实扫26108作品、26127目录、0错误。两组混合评分明确报整理方案未选共同质量规则，未替用户猜选。新增质量规则下拉、逐文件胜败/分数展开。真实宿主暴露页面查询别名不允许连字符，以及 rules.list nextOffset=-1代表结束，已在1.3.2修正；最终ready、UI200、status200、quality-rules查询成功，规则可选。未修改原整理方案规则、未清理败者、未执行整理/删除。保留既有规则资源授权；只增加rules.read能力。

上传曾触发 PLUGIN_PACKAGE_LIMIT：packages-inspections满100项。仅将最旧五份超过7天的inspect暂存移至容器 /app/data/plugins/inspection-archive-20261112/，没有删除已安装包。

对照 core/dedup_scan.py:729-824、core/mixed_link.py:263-345 和 core/dedup_submit.py：
- 已对齐：电影槽位/TV季集+槽位、未知集跳过、同分http优先、force_dedup忽略最小分差、同目录可有胜败、剩余双链chaos、评分失败保护。新增test_mixed_console_parity.py从实际mt-console AST提取分桶决策，用相同条目/规则对照胜败、分数、桶和chaos。此测试不等于完整端到端一致。
- 现有改善：败者文件级删除而非整目录；预提交复评分/核对fileRef；任务引用/未知结果锁/严格完成确认；云盘按作品映射+精确相对stem匹配，绝不使用STRM目标构造删除地址（遵循用户既定设计，与console原STRM目标删除策略刻意不同）。
- 仍待完善P0：尚未配置rootBindings；真实清理/整理未验证；缺少任务锁人工核对恢复入口；清理需对照确认伴随文件、同stem不同编码的歧义。
- P1：console会清理http同名nfo/jpg/png/mediainfo与安全空壳，插件目前只删败者主文件；云盘CD2同步后本地索引可能滞后，插件必须等待复扫，不可把宿主任务done等同本地索引清完；尚未实现自动清理→复扫→胜者整理一键链。
- P1：console parse-test(full)最终分类校验，插件SDK尚无等价能力，不能以目录多数票替代；本地ignore_paths/max_depth虽在scanner支持但工作台尚未完整暴露；取消/恢复/进度体验待补。
- P2：大混合组逐文件详情重复存储会触及单条分片上限；应拆成独立结果数据集并分页，评分计划GC、快照代次绑定选择/翻页也待完善。
- 规则选择：显式mixedRuleId优先，否则仅在已选整理方案共同选中唯一质量规则时使用；console使用自己strategy选定规则，因此两端必须选同规则才能比对，不自动替用户选择。

72项测试、Python3.12 check/mypy/pack通过（uv判断临时兼容补丁同前）。真实UI验证到API，不声称浏览器视觉验收。

## 1.3.4 混合组路由修正（2026-11-12）

用户确认混合组不应有独立整理方案或独立质量规则设置。删除 `mixedRuleId` 配置项、质量规则单独查询和页面选择器；混合评分现在只从常规本地/云盘整理方案的 `rules.effective` 读取质量规则。两方案必须有同一个已启用质量规则，否则明确报错，不发明默认规则。

评分只决定文件胜负；清理败者并复扫后，胜者沿普通路由：`http` 使用 `localProfileId/localDestDir`，`cloudnas`/CD2 使用 `cloudProfileId/cloudDestDir`。混合流程不新增 profile、目标目录或规则覆盖。1.3.4已原位部署，ready、UI/status正常，实例保持不变；74项测试、Python3.12 check/mypy/pack通过。未执行败者清理、整理或删除。

## 1.3.8 混合组选中兼容修复

删除混合组列表旁重复的评分选择器/保存按钮，仅保留前面折叠高级设置的一处。复选框根因经宿主status核对：1.3.7以前已保存的混合行没有group_key，rowKey均为空。现在读取快照/旧键时按media_type:tmdb_id补齐，无需重新扫描，也不重写完整结果。1.3.8实测旧两行已返回movie:580489和movie:912649；UI只含一个评分选择器。80项测试、Python3.12 check/mypy/pack通过；ready/UI200/status200。未做浏览器实际点击验证，未执行清理或整理。


## 当前版本 1.4.3 复核结论

当前使用方式以《当前使用说明.md》为准。已删除独立云盘连接模块、额外密钥声明与权限、旧连接配置及页面指引。云盘源操作仅通过MT文件SDK完成。公共槽为显示名称，内部评分键不变。

复核修正映射路径的穿越/反斜杠检查，以及包含保留项的云端目录禁止进入空壳删除。102项测试、Python3.12 check/mypy/pack通过，安装ready、UI/status200。测试数量变化包含移除废弃连接协议及映射测试、新增当前映射/发布清理测试，不是直接增加计数。未进行真实媒体修改。
