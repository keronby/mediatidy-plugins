from mt_plugin import Plugin as BasePlugin,Context,action,query

class Plugin(BasePlugin):
    @action("increment")
    def increment(self,input:dict,ctx:Context):
        count=ctx.mt.storage.get(key="count").get("value",0)+1
        ctx.mt.storage.set(key="count",value=count)
        ctx.mt.log(level="info",message=f"React 示例计数：{count}")
        return {"count":count}

    @query("history")
    def history(self,input:dict,ctx:Context):
        return {"count":ctx.mt.storage.get(key="count").get("value",0)}
