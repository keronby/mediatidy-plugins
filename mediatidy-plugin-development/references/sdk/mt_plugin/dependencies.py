"""Validate dependency metadata as data, without importing a plugin or build backend."""
from __future__ import annotations

from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import urlsplit

SDK_NAMES = {"mt-plugin", "mediatidy-plugin-sdk"}


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_dependencies(root: Path) -> None:
    require(sys.version_info[:2] == (3, 12), "Python 3.12 is required")
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    lock = tomllib.loads((root / "uv.lock").read_text("utf-8"))
    meta = project["project"]
    require(re.sub(r"\s+", "", meta["requires-python"]) in (">=3.12,<3.13", "==3.12.*"),
            "requires-python must select Python 3.12")
    require(not (root / "uv.toml").exists(), "uv.toml is unsupported; use pyproject.toml")
    tool = project.get("tool", {}).get("uv", {})
    require(not set(tool) - {"package", "dev-dependencies"}, "custom uv sources or installation options are unsupported")
    require(not tool.get("package", False), "the plugin project must not install itself")
    name = normalized(meta["name"])
    require(name not in SDK_NAMES, "host SDK cannot be replaced")
    dependencies = list(meta.get("dependencies", []))
    for group in meta.get("optional-dependencies", {}).values():
        dependencies.extend(group)
    dependencies.extend(tool.get("dev-dependencies", []))
    for group in project.get("dependency-groups", {}).values():
        dependencies.extend(dep for dep in group if isinstance(dep, str))
    for dep in dependencies:
        require(not any(char in dep for char in ("@", "://", "file:")), "URL/path dependencies are unsupported")
        match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", dep)
        require(match is not None and normalized(match[1]) not in SDK_NAMES, "host SDK cannot be replaced")
    packages = lock.get("package", [])
    require(bool(packages), "lock has no project")
    roots = 0
    for package in packages:
        package_name = normalized(package["name"])
        require(package_name not in SDK_NAMES, "host SDK cannot be replaced")
        source = package.get("source", {})
        if source == {"virtual": "."}:
            require(package_name == name, "invalid project source")
            roots += 1
            continue
        require(source == {"registry": "https://pypi.org/simple"}, "dependencies must use public PyPI")
        wheels = package.get("wheels", [])
        require(bool(wheels), "dependency has no prebuilt wheels: " + package_name)
        for artifact in [*wheels, *([package["sdist"]] if "sdist" in package else [])]:
            url = urlsplit(artifact["url"])
            require(url.scheme == "https" and url.netloc == "files.pythonhosted.org" and not url.query and not url.fragment,
                    "invalid dependency artifact URL")
            require(re.fullmatch(r"sha256:[a-f0-9]{64}", artifact["hash"]) is not None, "invalid artifact hash")
    require(roots == 1, "lock must contain exactly one virtual project")


if __name__ == "__main__":
    validate_dependencies(Path(sys.argv[1]))
    print("Python 3.12 / PyPI wheel lock checked")
