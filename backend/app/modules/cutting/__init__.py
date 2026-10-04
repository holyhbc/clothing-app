"""裁剪模块（docs/modules/02-裁剪.md）。

本包必须存在：``alembic/env.py`` 用 ``pkgutil.iter_modules`` 遍历子包来自动
import 各个 ``models``，少了 ``__init__.py`` 它就不是包，模型不会注册进
``target_metadata``，于是 ``alembic check`` 会认为这些表"不该存在"而建议删掉。
"""
