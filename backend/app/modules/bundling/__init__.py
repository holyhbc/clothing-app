"""打菲模块（docs/modules/03-打菲.md）。

本包必须存在：``app/common/models.py::register_all_models`` 与 ``alembic/env.py``
用 ``pkgutil.iter_modules`` 遍历子包来自动 import 各个 ``models``，少了
``__init__.py`` 它就不是包，模型不会注册进 ``Base.metadata``，于是
``alembic check`` 会认为四张打菲表"不该存在"而建议删掉。

⚠️ 本卡（T-BUND-001）**只建表与模型**，不写 repository / service / schema / router。
"""
