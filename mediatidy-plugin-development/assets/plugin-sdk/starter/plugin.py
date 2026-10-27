from mt_plugin import Plugin as BasePlugin, Context, action, query, continuation
from mt_plugin import ui


class Plugin(BasePlugin):
    def get_page(self, ctx: Context):
        return ui.page(
            ui.Card(ui.Text(text="选择授权目录，扫描任务会在后台执行。"), ui.FilePicker(),
                    ui.Button(label="扫描并保存清单", onClick=[{"type":"invoke","actionId":"scan-files"}])),
            ui.Table(source=ui.ref("query.history.data"), rowKey="ref", columns=[
                {"label":"文件名","field":"name","cells":[ui.Text(text=ui.ref("row.name"))]},
                {"label":"相对路径","field":"path","cells":[ui.Text(text=ui.ref("row.path"))]}]),
            ui.Progress(label=ui.ref("run.progress.message"),value=ui.ref("run.progress.completed"),total=ui.ref("run.progress.total")), ui.RunHistory(),
            queries=[{"id":"history","name":"history"}])

    @action("scan-files")
    def scan(self, input: dict, ctx: Context):
        tasks=[]
        files: list[dict] = [file for file in ctx.files if not file["isDir"]]
        for index, file in enumerate(ctx.files):
            if file["isDir"]:
                task=ctx.mt.files.scan(root_id=file["rootId"],path=file["path"],video_only=False,
                                      idempotency_key=f"{ctx.run_id}:scan:{index}")
                tasks.append(task["ref"])
        if tasks:
            ctx.mt.storage.set(key="pending/"+ctx.flow_id,value=files)
            ctx.mt.log(level="info",message=f"创建 {len(tasks)} 个扫描任务，等待结果")
            ctx.mt.tasks.wait(refs=tasks,completion={"action":"collect-report","input":{}})
        else:
            ctx.mt.storage.set(key="report",value=files)
            ctx.mt.log(level="info",message=f"已保存 {len(files)} 个所选文件")
        return {"waiting":bool(tasks)}

    @continuation("collect-report")
    def collect(self, input: dict, ctx: Context):
        files: list[dict] = ctx.mt.storage.get(key="pending/"+ctx.flow_id).get("value") or []
        for task in ctx.event["tasks"]:
            offset=0
            while True:
                result=ctx.mt.tasks.items(ref=task["ref"],offset=offset,limit=100)
                files.extend(item["file"] for item in result["items"] if item.get("file"))
                next_offset=result.get("nextOffset",0)
                if not next_offset or next_offset<=offset: break
                offset=next_offset
        ctx.mt.storage.set(key="report",value=files)
        ctx.mt.log(level="info",message=f"已保存 {len(files)} 个文件")
        return files

    @action("show-report")
    def show(self, input: dict, ctx: Context):
        return self.history(input,ctx)

    @query("history")
    def history(self, input: dict, ctx: Context):
        return ctx.mt.storage.get(key="report").get("value") or []
