from pathlib import Path
from PIL import Image
from mt_plugin import Plugin as BasePlugin, Context, action, continuation, query, ui

class Plugin(BasePlugin):
    def get_page(self, ctx: Context):
        return ui.page(
            ui.Card(ui.Text(text="选择单张图片与写入位置，结果会作为宿主任务提交。"),ui.FilePicker(),
                    ui.Input(label="输出授权目录 ID",bind="input.rootId"),ui.Input(label="输出相对路径（例如 resized.png）",bind="input.destination"),
                    ui.Input(label="最长边像素",bind="input.size",type="number"),
                    ui.Button(label="使用 Pillow 缩放",onClick=[{"type":"invoke","actionId":"resize"}])),
            ui.Image(src=ui.ref("query.latest.data.preview"),alt="最近处理结果"),ui.TaskList(),ui.RunLog(),
            ui.Button(label="刷新图片",onClick=[{"type":"query","queryId":"latest"}]),
            state={},queries=[{"id":"latest","name":"latest"}])

    @action("resize")
    def resize(self, input: dict, ctx: Context):
        files=[file for file in ctx.files if not file["isDir"]]
        if len(files)!=1: raise ValueError("请选择一张图片")
        source=ctx.download(files[0]["ref"])
        output=Path(ctx.work_dir)/"resized.png"
        with Image.open(source) as image:
            image.thumbnail((input["size"],input["size"]))
            image.save(output,"PNG")
        ctx.mt.log(level="info",message=f"Pillow 已缩放 {files[0]['name']}，正在交给宿主写入")
        task=ctx.upload(output,{"rootId":input["rootId"],"path":input["destination"]},idempotency_key=ctx.run_id+":image")
        ctx.mt.tasks.wait(refs=[task["ref"]],completion={"action":"collect","input":{}})
        return {"status":"waiting","taskRef":task["ref"]}

    @continuation("collect")
    def collect(self, input: dict, ctx: Context):
        task=ctx.event["tasks"][0]
        if task["status"]!="done": raise ValueError("图片写入未成功，请查看宿主任务")
        result=ctx.mt.tasks.items(ref=task["ref"],offset=0,limit=1)["items"][0]["file"]
        ctx.mt.storage.set(key="latest",value={"preview":"file:"+result["ref"],"file":result})
        ctx.mt.log(level="info",message="图片写回完成，可以预览")
        return result

    @query("latest")
    def latest(self, input: dict, ctx: Context):
        return ctx.mt.storage.get(key="latest").get("value",{})
