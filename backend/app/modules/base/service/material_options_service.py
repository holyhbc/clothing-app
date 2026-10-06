"""物料 / 供应商 / 布批候选（原 service.py 2543-2737）。"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.permissions import AuthContext
from app.core.scope import apply_data_scope
from app.modules.base.models import Material, MaterialStock, Supplier
from app.modules.base.schemas import OptionOut, StockBatchOptionOut

from .common import MAX_OFFSET, MAX_OPTION_SIZE

#: 物料候选的模糊搜索表达式：``code + name + 规格/类型``（05 §9.5.2「对编码 + 名称 +
#: 副标识做模糊匹配」）。
#:
#: ⚠️ **用 ``||`` 拼表达式而不是三个 ``ILIKE``**：三个 ``ILIKE`` 的 OR 里，只要有一项
#: 没有索引就会让 PG 扫全表；而拼接表达式能用上已有的表达式 GIN 索引
#: （与 :data:`STYLE_TRGM_QUALIFIED` 同一手法）。
MATERIAL_TRGM_QUALIFIED = (
    "(materials.code || ' ' || materials.name || ' ' || materials.material_type)"
)
SUPPLIER_TRGM_QUALIFIED = (
    "(suppliers.code || ' ' || suppliers.name || ' ' || coalesce(suppliers.short_name, ''))"
)


class MaterialOptionsService:
    """物料 / 供应商 / **布批候选**（T-BASE-007）。

    ## 为什么只有候选，没有列表 / 新建 / 停用

    ``docs/05 §9.5.2`` 只登记了这三个 ``/options``；物料与供应商的**档案维护**
    属 ``modules/01`` 的后续卡，而布批的增删改由**到货登记 / 采购单审核**产生
    （ADR-0012：无独立入库单）。刻意不给它们凑一套 CRUD —— 那会让「谁能改批次
    结存」这件事变成一个没经过业务拍板的默认值。

    ## 数据范围

    ⚠️ **这三个资源都不做车间过滤**，且这是有意的、不是漏登记：

    - ``materials`` / ``suppliers`` 是**全厂共享主数据**，与 ``colors`` / ``sizes``
      同性质（``SCOPE_SPECS`` 里它们是 ``ScopeSpec()``，即不加任何范围条件）。
    - ``material_stocks`` 按**仓库**隔离（``warehouse_id``），而仓库不是车间 ——
      ``app/core/scope.py`` 里已写明「库存的数据范围走仓库维度，见 modules/06」。
      登记成车间过滤会把「工厂级仓库」的库存凭空过滤掉。

    第一行仍然调 ``apply_data_scope``：它至少统一附加了 ``deleted_at IS NULL``
    （软删过滤，INV-7），并且表名将来登记进 ``SCOPE_SPECS`` 之后这里**自动**生效 ——
    靠「记得回来改这里」来保证的过滤迟早会漏。
    """

    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self.session = session
        self.ctx = ctx

    def _check_window(self, size: int, offset: int) -> None:
        """候选的 ``size`` / ``offset`` 边界（05 §9.5.2 与 ``OptionOut`` 家族一致）。

        ⚠️ **显式查而不是靠 FastAPI 的 ``Query(ge=1, le=20)``**：那个约束只在
        HTTP 层生效，而这三个方法将来也会被别的 service / 脚本调用 ——
        service 层的边界是这里唯一的那道。
        """
        if not 1 <= size <= MAX_OPTION_SIZE:
            raise BusinessError(
                ErrorCode.PARAM_INVALID, f"候选接口 size 必须在 1~{MAX_OPTION_SIZE} 之间"
            )
        if offset > MAX_OFFSET:
            raise BusinessError(ErrorCode.PARAM_INVALID, "offset 过大，请改用关键字搜索")

    async def list_material_options(
        self, keyword: str | None, size: int = MAX_OPTION_SIZE, offset: int = 0
    ) -> list[OptionOut]:
        """物料候选（05 §9.5.2）。

        ⚠️ **只返面料与辅料**：``material_type`` 是 ``FABRIC`` / ``TRIMMING`` /
        ``LABEL`` / ``FINISHED_GOODS``，而**成衣**不该出现在「裁剪要用的布料」
        选择器里 —— 让用户在一堆成衣里挑布料是最典型的「筛选没做」的表现。
        成衣库存另有候选入口。
        """
        self._check_window(size, offset)
        stmt = apply_data_scope(select(Material), Material, self.ctx).where(
            Material.material_type.in_(("FABRIC", "TRIMMING"))
        )
        if keyword:
            stmt = stmt.where(literal_column(MATERIAL_TRGM_QUALIFIED).ilike(f"%{keyword}%"))
        # ⚠️ 默认排序按 **编码升序**：物料没有「最近使用」字段（款号有
        #    ``last_used_at``，所以款号候选能按常用优先）。给一个不存在的字段排序
        #    会让候选顺序随机 —— 用户每次打开看到的次序不一样。
        stmt = stmt.order_by(Material.code.asc())
        rows = (await self.session.execute(stmt.offset(offset).limit(size))).scalars().all()
        return [
            OptionOut(
                value=row.code,
                # ⚠️ label 里**不要**再拼 material_type：Combo 的回显直接显示 label，
                #    而 sub 已经承担了副标识的位置（06 §10「label 含编码，不重复拼」）。
                label=f"{row.code} {row.name}",
                # ⚠️ **停用时 sub 让位给「已停用」**，与款号候选
                #    （``list_options`` 里 ``sub=None if row.is_active else '已停用'``）
                #    同一口径：Combo 只显示一个副文本位，而「这个不能选」比
                #    「它是面料」更需要被看见 —— 否则用户选完才收 20003。
                sub=row.material_type if row.is_active else "已停用",
                disabled=not row.is_active,
            )
            for row in rows
        ]

    async def list_supplier_options(
        self, keyword: str | None, size: int = MAX_OPTION_SIZE, offset: int = 0
    ) -> list[OptionOut]:
        """供应商候选（05 §9.5.2：``code`` / ``name`` / ``contact``）。

        ⚠️ 供应商**没有** ``is_active`` 列（停用语义在 modules/01 的后续卡里），
        所以 ``disabled`` 恒为 ``False``；别在这里 ``getattr(row, "is_active", True)``
        造一个「以为它有」的分支 —— 那会让「列真的加上之后要记得回来改」变成隐形的。
        """
        self._check_window(size, offset)
        stmt = apply_data_scope(select(Supplier), Supplier, self.ctx)
        if keyword:
            stmt = stmt.where(literal_column(SUPPLIER_TRGM_QUALIFIED).ilike(f"%{keyword}%"))
        stmt = stmt.order_by(Supplier.code.asc())
        rows = (await self.session.execute(stmt.offset(offset).limit(size))).scalars().all()
        return [
            OptionOut(value=row.code, label=f"{row.code} {row.name}", sub=row.short_name)
            for row in rows
        ]

    async def list_stock_batch_options(
        self,
        *,
        keyword: str | None = None,
        supplier_id: UUID | None = None,
        material_id: UUID | None = None,
        dye_lot_no: str | None = None,
        size: int = MAX_OPTION_SIZE,
        offset: int = 0,
    ) -> list[StockBatchOptionOut]:
        """布批候选（05 §9.5.2 末行 + C38 + BR-ST-17）。

        ⚠️ **三处口径都是规范强制的，不是这里的选择**：

        1. **必须 ``available_qty > 0``**（05 §9.5.2 加粗、ADR-0022）。写成本地表达式
           ``stock_qty - locked_qty > 0`` 而不是取出来在 Python 里比 ——
           那样会把「只剩 0.001 米的批次」也列出来，用户填完耗料才收 `40006`。
           这个表达式与部分索引 ``idx_material_stocks_pick`` 的谓词**逐字一致**，
           改一边不改另一边就会从「索引扫描」退化成「全表扫 + 过滤」。
        2. **默认排序 = FIFO**（BR-ST-17 ③：默认按入库日升序）。**不是**入库日倒序 ——
           先入库的先用，倒序等于让最陈旧的布永远排最后。
        3. **只返缸号 / 匹号 / 门幅 / 可用量**，**不返 ``unit_cost``**（05 §9.5.2、
           C38）。批次成本属财务口径，出现在一个选批下拉里就会被截图发到群里。
        """
        self._check_window(size, offset)
        available = MaterialStock.stock_qty - MaterialStock.locked_qty
        # ⚠️ **刻意不过滤 ``purpose``**：BR-ST-25 明写「``purpose='REWORK_RECEIPT'``
        #    的批次与正常布料一样**可被裁剪单选批领用**」（TC-35 ① 要求两批都能选到），
        #    而「RETURN / SAMPLE 能不能被裁」规范里**没写**。加一个 `purpose='NORMAL'`
        #    的过滤会把返修布挡在门外 —— 那是一个看起来很合理的判断，却与 BR-ST-25
        #    直接冲突且没有任何报错。所以这里只按 05 §9.5.2 强制的那一条过滤
        #    （``available_qty > 0``），把 ``purpose`` 作为**副标识返回**让录入员自己看。
        #    RETURN / SAMPLE 的取舍登记在 docs/12 待决问题里。
        stmt = apply_data_scope(select(MaterialStock), MaterialStock, self.ctx).where(available > 0)
        if supplier_id is not None:
            stmt = stmt.where(MaterialStock.supplier_id == supplier_id)
        if material_id is not None:
            stmt = stmt.where(MaterialStock.material_id == material_id)
        if dye_lot_no:
            # ⚠️ **精确匹配缸号**而不是模糊：缸号是编号，模糊匹配会把
            #    「H2408」和「H24080」同时列出来，而它们是**不同的布**。
            #    想要模糊的是用户手里的单号片段，那走 ``keyword``（匹号 / 物料名）。
            stmt = stmt.where(MaterialStock.dye_lot_no == dye_lot_no)
        if keyword:
            # ⚠️ 用 ``or_(...)``（SQLAlchemy 2.0 的函数式写法）而不是 ``stmt.or_(...)``：
            #    后者是 SQLAlchemy 1.4 的 API，2.0 已移除 —— 而**它要到真的传了
            #    ``q`` 才会炸**（`AttributeError: 'Select' object has no attribute 'or_'`），
            #    也就是「只按 material_id / 缸号筛」的用例全绿、用户手输关键字就 500。
            #    被 mypy 抓到，不是被测试抓到。
            stmt = stmt.where(
                or_(
                    MaterialStock.dye_lot_no.ilike(f"%{keyword}%"),
                    MaterialStock.bolt_no.ilike(f"%{keyword}%"),
                )
            )
        stmt = stmt.order_by(
            MaterialStock.in_date.asc(),
            MaterialStock.dye_lot_no.asc(),
            MaterialStock.bolt_no.asc(),
        )
        rows = (await self.session.execute(stmt.offset(offset).limit(size))).scalars().all()
        return [
            StockBatchOptionOut(
                value=str(row.id),
                # ⚠️ label 里带可用量与门幅：选批是「看着布挑」的活，
                #    只有一个匹号的话用户得逐个点开才知道门幅够不够（C24）。
                label=f"{row.dye_lot_no}/{row.bolt_no}",
                # ⚠️ ``purpose`` 进 ``sub``：返修布（REWORK_RECEIPT）能正常领用但成本
                #    走 5403 单独口径（BR-ST-25），录入员该在选批时就看见。
                sub=str(row.purpose),
                disabled=False,
                dye_lot_no=row.dye_lot_no,
                bolt_no=row.bolt_no,
                width_cm=row.width_cm,
                purpose=str(row.purpose),
                available_qty=(row.stock_qty or Decimal("0")) - (row.locked_qty or Decimal("0")),
                material_id=row.material_id,
                supplier_id=row.supplier_id,
            )
            for row in rows
        ]
