"""Loser-only shell policy: no STRM or regular media anywhere in the tree."""
import os
from pathlib import Path
from .organizer import VIDEO

MEDIA = VIDEO + ('.strm', '.vob', '.mts', '.m4v', '.rm', '.asf', '.m2t', '.m2v')


def local_inventory(work, boundary):
    from .mixed_pipeline import fingerprint, fingerprint_dir
    fingerprint_dir(work, boundary)
    files, directories = {}, []
    def fail(exc):
        raise exc
    for root, dirs, names in os.walk(work, followlinks=False, onerror=fail):
        fingerprint_dir(root, boundary)
        directories.append(root)
        for name in dirs:
            fingerprint_dir(os.path.join(root, name), boundary)
        for name in names:
            path = os.path.join(root, name)
            identity = fingerprint(path, boundary)
            if name.lower().endswith(MEDIA):
                raise ValueError('败者目录仍有STRM或正规媒体文件，禁止空壳清理')
            files[path] = identity
    return files, directories


def clean_local(work, boundary):
    """Read full tree twice before unlink; never follow links or remove root."""
    files, directories = local_inventory(work, boundary)
    if local_inventory(work, boundary) != (files, directories):
        raise ValueError('败者空壳目录已变化')
    from .mixed_pipeline import fingerprint
    for path, identity in files.items():
        if fingerprint(path, boundary) != identity:
            raise ValueError('败者空壳文件已变化')
        os.unlink(path)
    for path in sorted(directories, key=lambda p: len(Path(p).parts), reverse=True):
        os.rmdir(path)  # concurrent additions prevent directory removal


def cloud_inventory(client, work):
    from .mixed_pipeline import cloud_tree
    files, directories = cloud_tree(client, work)
    if any(item['path'].lower().endswith(MEDIA) for item in files):
        raise ValueError('云端败者目录仍有STRM或正规媒体文件，禁止空壳清理')
    return sorted(item['path'] for item in files), sorted(directories)
