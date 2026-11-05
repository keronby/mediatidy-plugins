"""Recognition workspace, composed with the public Python UI SDK."""
from mt_plugin import ui


def host(name):
    return [{"type": "host", "name": name}]


# 状态 → (文案, 色调)
STATUS_TEXT = {
    "pending": "等待识别", "recognized": "识别完成，待核对",
    "group_pending": "等待同剧核对", "verifying": "核对 TMDB",
    "queued": "整理已排队", "running": "处理中", "waiting": "等待整理",
    "submitted": "已提交", "submitting": "提交中",
    "done": "已完成", "ready": "待确认", "success": "已完成", "completed": "已完成",
    "needs_review": "需人工处理", "outcome_unknown": "结果待核对",
    "submission_unknown": "提交待核对", "interrupted": "已中断",
    "failed": "失败", "identity_conflict": "同剧识别冲突，未整理",
    "organize_failed": "整理失败", "verification_failed": "核对失败，已跳过",
    "cancelled": "已取消", "unrecognized": "未识别，已跳过",
}

# 5 个色带，每带一个 Badge：Badge 数固定 5+1，不随状态数增长
# （宿主单列 cells 上限 20 项，一个状态一个 Badge 会超限）
_TONE_GROUPS = [
    ("primary", ["pending", "recognized", "group_pending", "verifying",
                 "queued", "running", "waiting", "submitted", "submitting"]),
    ("success", ["done", "ready", "success", "completed"]),
    ("warning", ["needs_review", "outcome_unknown", "submission_unknown", "interrupted"]),
    ("danger", ["failed", "identity_conflict", "organize_failed", "verification_failed"]),
    ("muted", ["cancelled", "unrecognized"]),
]


def _labelled(status, keys):
    """在一个色带内拼出当前状态的文案，链长恒定 2 层。

    做法：两两配对成 1 层 if，再把这些 1 层 if 串成 1 层 -> 总深度 2。
    千万不要逐状态串成 N 层 if 链（9 个状态就是 9 层，必撞 maxDepth=12）。
    """
    leaves = [ui.expr("if", ui.expr("eq", status, k), STATUS_TEXT[k], "")
              for k in keys]
    while len(leaves) > 1:
        merged = []
        for i in range(0, len(leaves), 2):
            nxt = leaves[i + 1] if i + 1 < len(leaves) else ""
            merged.append(ui.expr("if", leaves[i], leaves[i], nxt))
        leaves = merged
    return leaves[0] if leaves else ""


def badges(path, fallback="状态待核对", status_expr=None):
    """状态徽标：每个色带一个 Badge。

    注意 status 若是「表达式」（如 coalesce(taskStatus, status)），它本身占 1 层，
    会叠加进每个 eq 里，可能顶到 maxDepth —— 这种情况用 status_expr 传入，
    并配合 fallback 使用。

    背景：宿主 maxDepth=12 是「组件树深度 + props 内表达式深度」叠加算的，
    Table 已在 Tab>Card 内第 4~5 层；同时单列 cells 上限 20 项。
    所以既不能把所有状态串成一条 24 层 if 链，也不能一个状态一个 Badge。
    """
    if status_expr is not None:
        status = status_expr
    elif isinstance(path, str):
        status = ui.ref(path)
    else:
        status = path
    result = []
    for tone, keys in _TONE_GROUPS:
        result.append(ui.Badge(text=_labelled(status, keys), tone=tone,
                               visible=ui.expr("includes", keys, status)))
    result.append(ui.Badge(text=fallback, tone="muted",
                           visible=ui.expr("eq", status, "")))
    return result


def result_columns():
    # 1.x stored only file/status; JS 2.x stored identity fields on the row.
    # Read those historical formats without inventing missing recognition data.
    legacy = ui.expr("and", ui.ref("row.file"), ui.expr("not", ui.ref("row.name")))
    field = lambda name: ui.expr("coalesce", ui.ref("row.detail.identity." + name), ui.ref("row." + name))
    title = ui.expr("coalesce", ui.ref("row.title"), field("title"))
    year, kind = field("year"), field("mediaType")
    season, episode = field("season"), field("episode")
    reason = ui.expr("if", legacy, ui.ref("row.status"), field("reason"))
    path = ui.expr("coalesce", ui.ref("row.detail.path"), ui.ref("row.path"))
    task = ui.expr("coalesce", ui.ref("row.detail.taskRef"), ui.ref("row.taskRef"))
    sources = field("sources")
    # 注意：Table 位于 Tab>Card 之内（组件树第 5 层），宿主 maxDepth=12 是
    # 「组件树深度 + props 内表达式深度」叠加计算的。因此列内表达式必须
    # 尽量扁平：能拆成多个 Text 就不要串成一个 concat 套 concat。
    kind_text = ui.expr("if", ui.expr("eq", kind, "movie"), "电影",
                        ui.expr("if", ui.expr("eq", kind, "tv"), "剧集", ""))
    season_text = ui.expr("coalesce", ui.ref("row.seasonEpisode"),
                          ui.expr("concat", "S", ui.expr("coalesce", season, "?")))
    episode_text = ui.expr("concat", "E", ui.expr("coalesce", episode, "?"))
    year_dot = ui.expr("if", year, ui.expr("concat", year, " · "), "")
    return [
        {"label": "片源文件", "field": "name", "cells": [
            ui.Text(text=ui.expr("coalesce", ui.ref("row.name"), ui.ref("row.file"), "未保存文件名")),
        ]},
        {"label": "识别作品", "field": "title", "cells": [
            ui.Text(text=ui.expr("if", title, title, ui.expr("if", legacy, "旧版记录未保存作品字段", "未确定作品"))),
            ui.Text(text=ui.expr("concat", year_dot, kind_text), tone="muted", size="sm", visible=ui.expr("not", legacy)),
            ui.Text(text=season_text, tone="muted", size="sm",
                    visible=ui.expr("and", ui.expr("eq", kind, "tv"), ui.expr("not", legacy))),
            ui.Text(text=episode_text, tone="muted", size="sm",
                    visible=ui.expr("and", ui.expr("eq", kind, "tv"), ui.expr("not", legacy))),
            ui.Text(text=ui.expr("concat", "TMDB · ", field("tmdbId")), tone="muted", size="sm", visible=field("tmdbId")),
        ]},
        {"label": "处理状态", "field": "status", "cells": [
            ui.Badge(text="AI 识别完成", tone="success", visible=ui.expr("eq", ui.ref("row.recognitionStatus"), "recognized")),
            ui.Badge(text="TMDB 核对通过", tone="success", visible=ui.expr("eq", ui.ref("row.detail.identityVerified"), True)),
            ui.Text(text=ui.expr("concat", "元数据核对：", ui.ref("row.detail.metadataCheckMs"), " ms"), tone="muted", size="sm",
                    visible=ui.expr("not", ui.expr("not", ui.ref("row.detail.metadataCheckMs")))),
            *badges("row.status", ui.expr("if", legacy, "旧版摘要", "状态待核对")),
            ui.Text(text=ui.ref("row.error"), tone="muted", size="sm", visible=ui.ref("row.error")),
            ui.Text(text=ui.ref("row.warning"), tone="warning", size="sm", visible=ui.ref("row.warning")),
            ui.Text(text=ui.expr("concat", "整理耗时（含排队）：", ui.ref("row.detail.organizeDurationMs"), " ms"), tone="muted", size="sm",
                    visible=ui.expr("not", ui.expr("not", ui.ref("row.detail.organizeDurationMs")))),
        ]},
        {"label": "识别说明", "cells": [
            ui.Text(text=ui.expr("if", reason, reason, "未保存识别说明")),
            ui.Markdown(text=ui.ref("row.detail.sourceMarkdown"), visible=ui.ref("row.detail.sourceMarkdown")),
            # 拆分：用 visible 各自判定，避免 or(path, task, gt(length(sources),0)) 这类深嵌套
            ui.Details(
                ui.Text(text=ui.expr("concat", "文件路径：", path), size="sm", visible=path),
                ui.Text(text=ui.expr("concat", "任务引用：", task), size="sm", visible=task),
                ui.Text(text=sources, size="sm", visible=sources),
                label="来源与任务信息",
                visible=ui.expr("or", path, task, sources),
            ),
        ]},
    ]


def draft_columns():
    title = ui.expr("coalesce", ui.ref("row.media.title"), ui.ref("row.recognition.title"))
    status = ui.expr("coalesce", ui.ref("row.taskStatus"), ui.ref("row.status"))
    return [
        {"label": "片源文件", "cells": [ui.Text(text=ui.ref("row.file.name"))]},
        {"label": "识别作品", "cells": [
            ui.Text(text=ui.expr("if", title, title, "未确定作品")),
            ui.Text(text=ui.expr("concat", "TMDB · ", ui.ref("row.tmdbId")), tone="muted", size="sm",
                    visible=ui.ref("row.tmdbId")),
            ui.Text(text=ui.expr("concat", "S", ui.ref("row.season")), tone="muted", size="sm",
                    visible=ui.expr("eq", ui.ref("row.mediaType"), "tv")),
            ui.Text(text=ui.expr("concat", "E", ui.ref("row.episode")), tone="muted", size="sm",
                    visible=ui.expr("eq", ui.ref("row.mediaType"), "tv")),
        ]},
        {"label": "核验与整理状态", "cells": [
            # 深度优先：taskStatus 与 status 各出一组，避免 coalesce(1层)+eq(1层) 叠进每个色带
            *badges("row.taskStatus"),
            ui.Text(text="", visible=False),
            *badges("row.status"),
            ui.Text(text=ui.ref("row.error"), tone="danger", size="sm", visible=ui.ref("row.error")),
        ]},
        {"label": "识别说明", "cells": [ui.Text(text=ui.expr("if", ui.ref("row.reason"), ui.ref("row.reason"), "未保存识别说明"))]},
    ]


def pagination(query_id, flow_key):
    data = f"query.{query_id}.data"
    return ui.Stack(
        ui.Button(label="上一页", variant="outline", disabled=ui.expr("lte", ui.ref(data+".page"), 1),
            onClick=[{"type": "set", "path": f"state.{query_id}Flow", "value": flow_key},
                     {"type": "set", "path": f"state.{query_id}Page", "value": ui.ref(data+".previous")}]),
        ui.Text(text=ui.expr("concat", "第 ", ui.ref(data+".page"), "/", ui.ref(data+".pages"), " 页 · 共 ", ui.ref(data+".total"), " 项")),
        ui.Button(label="下一页", variant="outline", disabled=ui.expr("gte", ui.ref(data+".page"), ui.ref(data+".pages")),
            onClick=[{"type": "set", "path": f"state.{query_id}Flow", "value": flow_key},
                     {"type": "set", "path": f"state.{query_id}Page", "value": ui.ref(data+".next")}]),
        direction="row", gap="sm", visible=ui.expr("gt", ui.ref(data+".pages"), 1))


def recognition_sections():
    """把「原石界面」的识别分区拆成节点列表 + state + queries，交给主插件拼 Tab。

    用户口径（2026-10-30）：第一个 Tab 就叫「AI 识别」，套用原石（official.ai-organize）
    的完整界面 —— 选择片源 / 整理设置 / 本次识别 / 识别结果 全部原样保留，
    这样它既是日常识别入口，也能承担「sha1 匹配」的手动识别页面。

    注意：不能直接把 ui.page(...) 塞进 ui.Tabs —— Tabs 的子节点必须是 Tab，
    塞整个 page 会报 INVALID_UI_PAGE（page: JSON 无效…）。
    """
    page = recognition_page()
    # ui.page 的正文在 page["content"]（列表），不是 page["children"]；
    # 直接传 children 会把列表当成单个子节点，报 "必须为对象"。
    # 另外原页里第一个子节点是 ui.Page（独立插件时代的页面容器），
    # 嵌进 Tab 会重复容器，这里把它拆开、只取其子节点。
    kids = []
    for node in (page.get("content") or []):
        if isinstance(node, dict) and node.get("component") == "Page":
            kids.extend(node.get("children") or [])
        else:
            kids.append(node)
    # 只交出 sections / state / queries，由主插件决定塞进哪个 Tab。
    return {"sections": kids,
            "state": page.get("state") or {},
            "queries": page.get("queries") or []}


# 兼容旧名（主插件早期调用它把识别区块拼进「清理流程」）
recognition_tab = recognition_sections


def recognition_page():
    has_run = ui.ref("run.id")
    running = ui.expr("includes", ["queued", "running", "waiting", "submitting"], ui.ref("run.status"))
    rows = ui.expr("if", ui.ref("query.results.data.available"), ui.ref("query.results.data.items"), ui.ref("run.result"))
    has_rows = ui.expr("gt", ui.expr("length", rows), 0)
    has_drafts = ui.expr("gt", ui.expr("length", ui.ref("run.drafts")), 0)
    can_retry = ui.expr("and", ui.expr("not", running), ui.ref("query.retry.data.count"))
    flow_key = ui.expr("if", has_run,
        ui.expr("concat", "ai/flow/", ui.expr("coalesce", ui.ref("run.flowId"), ui.ref("run.id"))), "")
    has_responses = ui.expr("gt", ui.expr("length", ui.ref("query.responses.data.items")), 0)
    review = ui.ref("input.review")
    return ui.page(
        ui.Page(
            ui.Grid(
                ui.Card(
                    ui.FilePicker(),
                    title="选择片源",
                    description="勾选视频文件或目录。每轮最多 500 个视频，自动按每批 8 个文件依次识别；超过 100 个需更新主程序。",
                ),
                ui.Card(
                    ui.ProfilePicker(label="整理到哪里", bind="input.profileId"),
                    ui.Select(label="识别后如何处理", bind="input.review", options=[
                        {"label": "识别后直接整理", "value": False, "description": "AI 识别完成后，按所选方案自动处理文件。"},
                        {"label": "整理前让我确认", "value": True, "description": "先核对作品、季集和目标位置；确认后才执行整理。"},
                    ]),
                    ui.Text(text=ui.expr("if", review,
                        "本次先识别。你确认结果前，不会执行文件整理。",
                        "识别成功后按方案执行整理，包括方案中设置的移动、覆盖和清理操作。"), tone="muted", size="sm"),
                    ui.Details(ui.Textarea(label="补充说明", bind="input.instructions",
                        placeholder="例如：这是某剧的第二季，文件名中的数字是集数。"), label="给 AI 补充线索 · 可选"),
                    # 不弹 Drawer：Drawer 放在页面 content 顶层会让整个页面白屏，
                    # 改成内联 Details 折叠面板（纯组件树，无浮层）。
                    ui.Details(
                        ui.Text(text="提示词决定 AI 如何识别作品。默认提示词已可直接使用，按需调整即可。"
                                     "保存后应用于之后的识别；当前任务的临时线索请填在上方「补充说明」。",
                                tone="muted", size="sm"),
                        ui.Textarea(label="完整识别提示词", bind="config.prompt"),
                        ui.Button(label="保存提示词", variant="outline", onClick=host("saveConfig")),
                        label="高级设置 · 识别提示词",
                    ),
                    ui.Divider(),
                    ui.Stack(
                        ui.Button(label=ui.expr("if", review, "开始识别，稍后确认", "开始识别并整理"),
                            disabled=ui.expr("or", ui.ref("plugin.busy"), ui.expr("not", ui.ref("input.profileId")), ui.expr("eq", ui.ref("stats.selections"), 0)),
                            onClick=[{"type": "invoke", "actionId": "recognize"}]),
                        ui.Text(text=ui.expr("if", ui.expr("eq", ui.ref("stats.selections"), 0),
                            "请先选择文件或目录", ui.expr("if", ui.ref("input.profileId"), "准备就绪，可以开始识别", "请选择整理方案")), tone="muted", size="sm"),
                        gap="sm",
                    ),
                    title="整理设置",
                    description="选择已授权的整理方案，告诉 AI 需要注意的线索。",
                ),
                columns=2, gap="lg", visible=ui.expr("not", has_run),
            ),
            ui.Card(
                ui.Stack(*badges("run.status"),
                    ui.Text(text=ui.ref("run.createdAt"), format="datetime", tone="muted", size="sm"),
                    direction="row", align="center", gap="sm"),
                ui.Progress(label=ui.expr("coalesce", ui.ref("run.progress.message"), "正在准备识别"),
                    value=ui.ref("run.progress.completed"), total=ui.ref("run.progress.total"), visible=running),
                ui.Alert(text=ui.ref("run.error"), tone="danger", visible=ui.ref("run.error")),
                ui.Text(text="重试沿用原方案与补充说明，识别成功后按原处理方式整理。已成功的结果保留在原运行记录。", tone="muted", size="sm", visible=can_retry),
                ui.Text(text=ui.ref("query.retry.data.message"), tone="muted", size="sm", visible=ui.ref("query.retry.data.message")),
                ui.Text(text="AI 结论先显示，再核对 TMDB。可归为同组的分集文件会等待整组识别完成；ID 或季号冲突时整组不整理，可重试整组识别。未识别文件保留原位；整理失败不会重新识别。", tone="muted", size="sm"),
                ui.Stack(
                    ui.Button(label=ui.expr("concat", "重试识别（", ui.ref("query.retry.data.count"), " 个文件）"), visible=can_retry,
                        onClick=[{"type": "invoke", "actionId": "retry-recognition", "input": {"flowKey": ui.ref("query.retry.data.flowKey")}}]),
                    ui.Button(label="修正或确认整理", visible=has_drafts, onClick=host("reviewDrafts")),
                    ui.Button(label="刷新结果", variant="outline", onClick=host("refreshRun")),
                    ui.Button(label="查看日志", variant="ghost", onClick=host("openLogs")),
                    ui.Button(label="取消本次运行", variant="outline", visible=running, onClick=host("cancelRun")),
                    ui.Button(label="开始新一轮", variant="outline", visible=ui.expr("not", running), onClick=host("newRun")),
                    direction="row", gap="sm",
                ),
                title="本次识别", visible=has_run,
            ),
            ui.Card(
                ui.Section(
                    ui.Table(source=ui.ref("run.drafts"), rowKey="id", searchable=True, pageSize=20, columns=draft_columns()),
                    ui.Text(text="需要修正时，点击上方「修正或确认整理」。核验通过并由你确认后才会提交整理。", tone="muted", size="sm"),
                    title="MT 核验与整理", visible=has_drafts,
                ),
                ui.Alert(text=ui.ref("query.results.error"), tone="danger", visible=ui.ref("query.results.error")),
                ui.Table(source=rows, rowKey="fileRef", searchable=True, sortable=True, pageSize=20,
                    visible=has_rows, columns=result_columns()),
                pagination("results", flow_key),
                ui.Text(text="搜索与排序作用于当前页。", tone="muted", size="sm", visible=ui.expr("gt", ui.ref("query.results.data.pages"), 1)),
                ui.Details(
                    ui.Text(text="保留每批最终回答的前 8 KB，便于核对解析错误或文件引用；旧版记录无法补回未保存的回答。", tone="muted", size="sm"),
                    ui.Button(label="刷新回答", variant="outline", onClick=[{"type": "query", "queryId": "responses"}]),
                    ui.Text(text="正在读取回答", visible=ui.ref("query.responses.loading")),
                    ui.Alert(text=ui.ref("query.responses.error"), tone="danger", visible=ui.ref("query.responses.error")),
                    ui.Text(text="本次记录没有保存 AI 最终回答。", tone="muted",
                        visible=ui.expr("not", ui.expr("or", has_responses, ui.ref("query.responses.loading"), ui.ref("query.responses.error")))),
                    ui.Table(source=ui.ref("query.responses.data.items"), rowKey="batch", visible=has_responses, columns=[
                        {"label": "批次", "cells": [ui.Text(text=ui.ref("row.batch"))]},
                        {"label": "解析诊断", "cells": [ui.Text(text=ui.ref("row.diagnosis"))]},
                        {"label": "最终回答", "cells": [ui.Details(
                            ui.Text(text=ui.expr("if", ui.ref("row.responseText"), ui.ref("row.responseText"), "（空回答）")),
                            ui.Text(text="回答较长，仅保留前 8 KB。", tone="muted", size="sm", visible=ui.ref("row.truncated")),
                            label="展开原始回答") ]},
                    ]),
                    pagination("responses", flow_key),
                    label="AI 原始回答与诊断",
                ),
                ui.EmptyState(
                    title=ui.expr("if", has_run, "还没有识别结果", "让每个文件，找到它的作品"),
                    description=ui.expr("if", running, "正在扫描或识别片源，结果会自动显示在这里。", ui.expr("if", has_run,
                        "本次运行尚未返回文件结果，可查看上方状态或运行日志。", "选择片源和整理方案后开始。作品、季集与处理结果将在这里集中展示。")),
                    visible=ui.expr("not", ui.expr("or", has_rows, has_drafts)),
                ),
                title="识别结果", description="作品、季集和识别说明直接展示。旧版历史记录保留原始摘要。", visible=has_run,
            ),
        ),
        state={
            # 分页状态由 pagination() 的 onClick 写入，必须在 state 里声明初始值
            "resultsFlow": "", "resultsPage": 1,
            "responsesFlow": "", "responsesPage": 1,
        },
        queries=[{"id": "retry", "name": "retry-state", "input": {"flowKey": flow_key}},
                 *[{"id": name, "name": name, "input": {"flowKey": flow_key,
                     "page": ui.expr("if", ui.expr("eq", ui.ref(f"state.{name}Flow"), flow_key), ui.expr("coalesce", ui.ref(f"state.{name}Page"), 1), 1),
                     "revision": ui.expr("coalesce", ui.ref("run.updatedAt"), "")}} for name in ("responses", "results")]],
    )
