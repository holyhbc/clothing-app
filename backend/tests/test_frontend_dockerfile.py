"""前端镜像 Dockerfile 的清单守卫（T-WEB-002 落地）。

背景：``docker/frontend/Dockerfile`` 的 builder 阶段为了保住层缓存
（docs/11 §4「依赖层先于源码层」），只 ``COPY`` 各 package 的 ``package.json``
就执行 ``pnpm install --frozen-lockfile``。而 **pnpm workspace 的锁文件里记着每个
importer**：漏拷任何一个包的清单，install 就会直接失败::

    ERR_PNPM_OUTDATED_LOCKFILE
      the lockfile records `importers["packages/admin"]`, but that project's
      directory or package.json is missing

这个错误极易在「加了新 package 但忘了改 Dockerfile」时发生，而它**只会在
Docker 构建里暴露** —— 本地 ``pnpm install`` / ``pnpm build`` 一切正常。
闸门 5 此前又漏掉了 ``web-image``（见 ``scripts/gate.sh`` 同批修复），于是这个
一眼可见的错误整整两个 T-INFRA + 一个 T-WEB 卡片都没被发现。

所以在这里把「新增 package 必须登记进 Dockerfile」变成**闸门 1 就会失败**的事。
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"
DOCKERFILE = REPO_ROOT / "docker" / "frontend" / "Dockerfile"
WORKSPACE_GLOB = "packages/*"


def workspace_packages() -> list[str]:
    """``pnpm-workspace.yaml`` 的 ``packages/*`` 下真实存在的包（按名排序）。"""
    root = FRONTEND / "packages"
    return sorted(p.name for p in root.iterdir() if (p / "package.json").is_file())


def dockerfile_manifest_copies() -> set[str]:
    """Dockerfile 里所有 ``COPY frontend/packages/<pkg>/package.json ...`` 的包名。"""
    if not DOCKERFILE.is_file():
        pytest.skip(f"{DOCKERFILE} 不存在（未初始化前端镜像）")
    content = DOCKERFILE.read_text(encoding="utf-8")
    return set(re.findall(r"COPY\s+frontend/packages/([^/\s]+)/package\.json", content))


def test_workspace_has_at_least_one_package() -> None:
    """没有包的话下面两个断言都会空转 —— 先确认 glob 真的匹配到了东西。"""
    assert workspace_packages(), f"{FRONTEND / WORKSPACE_GLOB} 下没有任何包"


def test_every_workspace_package_is_copied_in_dockerfile() -> None:
    missing = sorted(set(workspace_packages()) - dockerfile_manifest_copies())
    assert not missing, (
        f"新增 package 后忘了在 {DOCKERFILE.name} 的 builder 阶段加一行 "
        f"`COPY frontend/packages/<pkg>/package.json ./packages/<pkg>/`，"
        f"漏了 {missing}。不加则 `pnpm install --frozen-lockfile` 报 "
        f"ERR_PNPM_OUTDATED_LOCKFILE（锁文件里记着该 importer），"
        f"而**本地 pnpm build 是正常的**，只有 Docker 构建会炸。"
    )


def test_dockerfile_has_no_stale_package_copy() -> None:
    """反向：Dockerfile 里 COPY 了已删除的包，同样会让 install 失败。"""
    stale = sorted(dockerfile_manifest_copies() - set(workspace_packages()))
    assert not stale, f"{DOCKERFILE.name} 里 COPY 了不存在的包 {stale}（包已删除）"


def test_install_runs_before_source_copy() -> None:
    """install 必须早于 ``COPY frontend/ ./``，否则层缓存失效（docs/11 §4）。"""
    content = DOCKERFILE.read_text(encoding="utf-8")
    install = content.find("pnpm install")
    source = content.find("COPY frontend/ ./")
    assert install != -1, "Dockerfile 里找不到 pnpm install"
    assert source != -1, "Dockerfile 里找不到 `COPY frontend/ ./`（源码层）"
    assert install < source, "pnpm install 必须在拷源码之前，否则依赖层缓存永远失效"
