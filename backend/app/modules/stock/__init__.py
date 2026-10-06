"""库存模块（docs/modules/06-库存.md）。

本包必须存在：``alembic/env.py`` 用 ``pkgutil.iter_modules`` 遍历子包来自动
import 各个 ``models``，少了 ``__init__.py`` 它就不是包，模型不会注册进
``target_metadata``，于是 ``alembic check`` 会认为这些表"不该存在"而建议删掉。

⚠️ **本包目前只有 ``models.py``**：``docs/01 §4.2`` 的目录规划里 ``stock/`` 是
并列于 ``base/`` / ``cutting/`` 的模块，本卡（T-BASE-009）只为三张台账与锁定表建它。
``schemas`` / ``repository`` / ``service`` / ``router`` 随 P3 库存业务一起补 ——
现在建空壳只会让「模块已就位」看起来比实际完整。
"""
