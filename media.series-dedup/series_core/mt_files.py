"""MT-authorized mapped source access. No external credentials."""
import hashlib
import posixpath
from .storage import read_value, write_value
from .submitter import match_root

def mapped_path(path):
    if (not isinstance(path, str) or not path.startswith('/') or path == '/'
            or any(part in ('.', '..', '') for part in path[1:].split('/'))
            or '\\' in path):
        raise ValueError('MT映射路径必须为不含穿越的非根绝对路径')
    return path

class MTTasksPending(ValueError):
    """Known non-terminal task refs: wait, never resubmit mutations."""
    def __init__(self, refs):
        super().__init__('MT删除任务尚未明确成功；等待原任务续接，不重发删除')
        self.refs = refs


class MTFiles:
    def __init__(self, ctx, roots, key):
        self.ctx, self.roots, self.key = ctx, roots, key
        self.calls = 0
        limits = ctx.mt.runtime.info().get('limits') or {}
        self.limit = min(100, max(1, int(limits.get('maxWorkflowFiles', 100))))

    def location(self, path):
        mapped_path(path)
        root = match_root(path, self.roots)
        if not root:
            raise ValueError('映射目录缺少显式授权根绑定')
        rel = path[len(root['path'].rstrip('/')):].strip('/')
        if not rel:
            raise ValueError('禁止操作授权根目录')
        return root['id'], rel

    def check_tasks(self):
        pending = []
        for item in read_value(self.ctx, self.key, []):
            if not item.get('ref'):
                raise ValueError('MT删除提交结果未知，禁止重放；需核对原任务')
            task = self.ctx.mt.tasks.get(ref=item['ref'])
            status = task.get('status')
            if status in ('submitting', 'queued', 'running', 'waiting'):
                pending.append(item['ref'])
            elif status != 'done' or type(task.get('failed')) is not int or task['failed'] != 0:
                raise ValueError('MT删除任务尚未明确成功；需人工核对，不重发删除')
        if pending:
            raise MTTasksPending(list(dict.fromkeys(pending)))

    def list_files(self, path):
        self.check_tasks()
        mapped_path(path)
        root = match_root(path, self.roots)
        if not root:
            raise ValueError('映射目录缺少显式授权根绑定')
        rel = path[len(root['path'].rstrip('/')):].strip('/') or '.'
        rid = root['id']
        out = []
        for page in range(1, 101):
            self.calls += 1
            if self.calls > self.limit:
                raise ValueError('MT文件预检达到单轮预算，请缩小批次')
            result = self.ctx.mt.files.list(root_id=rid, path=rel, page=page)
            if not isinstance(result, dict) or not isinstance(result.get('items'), list) or type(result.get('hasMore')) is not bool:
                raise ValueError('MT目录响应不完整，不能证明目录为空')
            for item in result['items']:
                name = item.get('name')
                if not name or '/' in name or '\\' in name or name in ('.', '..') or type(item.get('isDir')) is not bool:
                    raise ValueError('MT目录条目非法')
                out.append(dict(item, path=posixpath.join(path, name)))
            if not result['hasMore']:
                return out
        raise ValueError('MT目录分页未完成，禁止使用截断结果')

    def delete_files(self, paths):
        self.check_tasks()
        rows = read_value(self.ctx, self.key, [])
        if not paths or len(set(paths)) != len(paths):
            raise ValueError('MT删除清单为空或重复')
        if len(rows) + len(paths) > self.limit:
            raise ValueError('MT删除累计数量超过本轮预算，请分批处理')
        prepared = []
        for path in paths:
            if any(item['path'] == path for item in rows):
                raise ValueError('该MT路径已有提交记录，禁止重放')
            rid, rel = self.location(path)
            self.calls += 1
            if self.calls > self.limit:
                raise ValueError('MT文件预检达到单轮预算，请缩小批次')
            item = self.ctx.mt.files.stat(root_id=rid, path=rel)
            if not item.get('ref'):
                raise ValueError('MT未返回fileRef')
            directory = item.get('isDir') is True
            if directory and self.list_files(path):
                raise ValueError('目录仍有子项，禁止提交空目录删除')
            prepared.append((path, item['ref'], directory))
        for path, ref, directory in prepared:
            # MT requires recursive=True for directory refs. Only submit after
            # a fresh complete listing proves it empty; files remain False.
            if directory and self.list_files(path):
                raise ValueError('空目录在提交前已变化，禁止删除')
            row = {'path':path, 'ref':None}
            rows.append(row)
            write_value(self.ctx, self.key, rows)  # before mutation; unknown never retried
            task = self.ctx.mt.files.delete(file_ref=ref, recursive=directory,
                idempotency_key='mixed-mt:' + hashlib.sha256((self.key+':'+ref).encode()).hexdigest())
            if not isinstance(task, dict) or not task.get('ref'):
                raise ValueError('MT删除提交结果未知，禁止重放')
            row['ref'] = task['ref']
            write_value(self.ctx, self.key, rows)
        self.check_tasks()
        return {'ok':True}
