"""字典内置库的业务数据常量（docs/04 §7.4 + modules/01 §3.6.4 定稿清单）。

本文件只承载**业务数据**（色卡 / 尺码 / 码表 / 商品分类 / 计量单位 / 物料类目），
与写入逻辑（:mod:`app.cli.seed_dicts.seeders`）、校验逻辑
（:mod:`app.cli.seed_dicts.checker`）物理分离 —— 业务方改一个色名只需评审本文件。
"""

from __future__ import annotations

# 字典内置库（docs/04 §7.4 + modules/01 §3.6.4 定稿清单）
# ==================================================================

#: 16 色基础色卡（modules/01 §3.6.4，业务方 2026-10-01 确认）
BUILTIN_COLORS: tuple[tuple[str, str], ...] = (
    ("WHT", "本白"),
    ("BLK", "黑"),
    ("NVY", "藏青"),
    ("KHK", "卡其"),
    ("GRN", "军绿"),
    ("BEG", "米色"),
    ("RED", "正红"),
    ("BLU", "蓝"),
    ("GRY", "深灰"),
    ("PNK", "粉"),
    ("PUR", "紫"),
    ("SLT", "浅灰"),
    ("BRN", "棕"),
    ("OLV", "橄榄"),
    ("BEG2", "浅卡其"),
    ("WHT2", "本白(裁剪区分)"),
)

#: 内置尺码 **8 行**（不是 6 行）：``L`` / ``XL`` 在女款与男款各一行。
#: 唯一键含 ``size_class``（ADR-0027），所以同一码可以存两份。
#: ``name`` 用实际围度标注（04 §7.4 把括号内围度列为"可选"，§3.6.4 要求"用实际尺码标注"）。
BUILTIN_SIZES: tuple[tuple[str, str, str, int], ...] = (
    # (size_code, size_class, name, sort_order)
    ("S", "WOMENS", "S(155/80A)", 1),
    ("M", "WOMENS", "M(160/84A)", 2),
    ("L", "WOMENS", "L(165/88A)", 3),
    ("XL", "WOMENS", "XL(170/92A)", 4),
    ("L", "MENS", "L(165/88A)", 1),
    ("XL", "MENS", "XL(170/92A)", 2),
    ("XXL", "MENS", "XXL(175/96A)", 3),
    ("3XL", "MENS", "3XL(180/100A)", 4),
)

#: 只内置 2 个码表（业务方明确）。成员**各指向本尺码类的那一行**（ADR-0027）
BUILTIN_SIZE_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("女款模板 S-M / L-XL", "WOMENS", ("S", "M", "L", "XL")),
    ("男款模板 L XL XXL 3XL", "MENS", ("L", "XL", "XXL", "3XL")),
)

#: 6 个内置商品分类（04 §7.11 / ADR-0020）
BUILTIN_PRODUCT_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    ("SET", "套装", 1),
    ("DRESS", "单衣", 2),
    ("TROUSERS", "单裤", 3),
    ("UNDERWEAR", "棉毛", 4),
    ("VEST", "背心", 5),
    ("THERMAL", "打底裤", 6),
)

#: 计量单位（``04 §7.14.1`` 的建表注释里明确列了这三个）。
#:
#: ⚠️ **只搬规范里已写明的，不自行扩充**：``04 §7.14.1`` 的注释给的是
#: ``'PCS' 件 / 'M' 米 / 'KG' 千克``，所以就种这三个。面料行业常用的 ``YD``（英码）
#: **故意不加** —— 它没在任何规范里出现过，而「度量衡码表该有哪些」是业务方的口径
#: （有的厂按米、有的按码、有的按公斤报价）。用户可以在界面上自建。
#:
#: ⚠️ 这三行是 T-BASE-005 顺带发现的缺口：**表建了但一直是空的**，而
#: ``materials.uom_unit_id`` 是必填外键 —— 也就是说物料档案**一条都建不了**。
#: 空表 + 必填外键的组合，症状是「页面打开就报外键违反」，而报错完全看不出
#: 「根因是码表没有初始数据」。
BUILTIN_UOM_UNITS: tuple[tuple[str, str, int], ...] = (
    # code, name, decimal_places（04 §7.14.1 无 sort 列）
    ("M", "米", 3),
    ("KG", "千克", 3),
    ("PCS", "件", 0),
)

#: 物料类目（09 §2.3 物料编码 ``F-{类目码}-{6 位}`` 的第二段；DDL 见 04 §7.15.1）。
#:
#: ⚠️ 这是**种子候选值**而不是封闭枚举 —— 用户可以按自己的料号体系加 ``ZZ-1``。
#:    封闭枚举会逼着用户改编码规则，而 09 §2.2 明写物料编码「不做格式正则校验」。
#:    真正必须在代码里分支的是 ``materials.material_type``（那个是 PG 枚举）。
BUILTIN_MATERIAL_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    ("CT", "纯棉布", 1),
    ("TW", "混纺布", 2),
    ("KN", "针织布", 3),
    ("FL", "毛料", 4),
    ("DM", "牛仔布", 5),
    ("ZL", "拉链", 6),
    ("NX", "纽扣", 7),
    ("WD", "织带", 8),
    ("TH", "缝纫线", 9),
    ("LB", "唛头", 10),
)

#: 操作人（seed 是运维动作，不存在真实登录用户）
_SEED_OPERATOR = "00000000-0000-0000-0000-000000000001"


#: 字典表 → ``document_logs.doc_type``。
#:
#: ⚠️ 必须与 ``DictResource.doc_type`` 逐字一致 —— 墓碑判定靠这个值匹配
#: ``document_logs``，写成表名会导致"服务写了 DELETE 墓碑、seed 认不出来"，
#: 于是被真删的内置项复活（TC-B21）。
DICT_DOC_TYPES: dict[str, str] = {
    "colors": "Color",
    "sizes": "Size",
    "size_groups": "SizeGroup",
    "product_categories": "ProductCategory",
    # ⚠️ 类目码表**当前不在 ADR-0025 的硬删白名单里**（04 §7.15.1），所以理论上
    #    不会有真删墓碑。仍然登记在这里的原因：**白名单以后放开时不需要改两处**
    #    —— 墓碑跳过逻辑先摆好，别等放开那天才想起来（那时数据已经写坏了）。
    "material_categories": "MaterialCategory",
}
