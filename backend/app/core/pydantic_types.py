"""跨模块共用的 pydantic 类型片段（``docs/05 §3`` 的「响应里数量 / 金额一律 ``str``」）。

## 为什么要抽出来而不是各模块各写一份

两个模块各自定义了同名的 ``Str``（T-CUT-001c-1 在 ``cutting/schemas.py`` 遇到的坑）：
pydantic v2 的 ``str`` **不接受** ``Decimal``，而响应是从 ORM 对象
``model_validate`` 出来的，属性是 ``Decimal`` —— 直接标 ``str`` 报
``Input should be a valid string [input_value=Decimal('96.000')]``，
而这条报错完全看不出根因是「出参类型不能直接从 ORM 构造」。

留着两份拷贝的后果不是「重复了一行代码」，而是**下一个模块会再踩一次同一个坑**
（第三份会带着自己的注释，注释里大概率写着另一套理由）。所以定义一次。
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator

#: 响应里的数量 / 金额：标 ``str``（前端能拿到 ``'0.378000'`` 的完整精度），
#: 但**接受** ORM 里的 ``Decimal`` / ``int``。
#:
#: ⚠️ 只用于**出参**。入参不要用它 —— 入参本来就该由前端传字符串，
#: 而 ``str`` 类型对「非字符串」的静默转换会让 ``hands=1.5`` 变成 ``'1.5'``：
#: 一个该报 ``10001`` 的请求被悄悄接受了。
Str = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, (Decimal, int)) else v)]
