"""Local STRM scan input, independent of the host file/workflow APIs."""
import os


def scan_path(value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('请填写插件运行环境内的本地绝对路径')
    path = value.strip()
    if not os.path.isabs(path):
        raise ValueError('扫描路径必须为本地绝对路径')
    return os.path.normpath(path)


def saved_path(config):
    # Preserve pre-picker local configuration without inventing mount bindings.
    return config.get('localScanPath') or config.get('rootPath') or ''
