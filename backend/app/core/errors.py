"""业务异常与错误码（docs/05-接口设计规范.md §4）。

铁律（AGENTS.md §2.1）：
    - **禁止裸 ``raise Exception``**，业务失败一律抛 :class:`BusinessError`
    - **错误码不允许临时编造**：``ErrorCode`` 的取值必须是 docs/05 §4 已登记的码。
      单测 ``tests/test_errors_registry.py`` 会解析 docs/05 §4 表格做双向断言。

命名规范（docs/03 §1.2）：枚举成员用大写下划线，与错误码语义一一对应。
"""

from enum import IntEnum
from http import HTTPStatus
from typing import Any


class ErrorCode(IntEnum):
    """业务错误码。**分段规则**：``HTTP 状态 × 业务域 × 序号``（docs/05 §4）。

    本卡（T-INFRA-003）只登记 **通用段 10xxx / 认证段 11xxx / 权限段 12xxx /
    基础资料段 20xxx**；30xxx~60xxx 由各业务模块在自己的 models.py 就近补充，
    但必须同样登记在本枚举里（禁止散落魔法数字，docs/03 §1.1 第 7 条）。
    """

    OK = 0

    # ---- 10xxx 通用 / 参数 ----
    PARAM_INVALID = 10001
    MISSING_BUSINESS_PARAM = 10002
    OPTIMISTIC_LOCK_CONFLICT = 10003
    TOO_MANY_REQUESTS = 10004
    SELF_APPROVAL_FORBIDDEN = 10005
    REASON_REQUIRED = 10006
    ILLEGAL_STATUS_TRANSITION = 10007
    ILLEGAL_OPERATION = 10008
    IMPORT_ROWS_EXCEEDED = 10009

    # ---- 11xxx 认证 ----
    UNAUTHORIZED = 11001
    TOKEN_INVALID = 11002
    PASSWORD_INCORRECT = 11003
    ACCOUNT_DISABLED = 11004
    IMPORT_FILE_FORMAT_ERROR = 11007
    IMPORT_DATA_INVALID = 11008
    EXPORT_RANGE_TOO_LARGE = 11011

    # ---- 12xxx 权限 ----
    PERMISSION_DENIED = 12001
    DATA_SCOPE_DENIED = 12002

    # ---- 20xxx 基础资料 ----
    BASE_DATA_NOT_FOUND = 20001
    STYLE_ALREADY_EXISTS = 20002
    BASE_DATA_REFERENCED = 20003
    OPERATION_RATE_NOT_SET = 20004
    RATE_RANGE_OVERLAP = 20005
    SIZE_RATIO_INCOMPLETE = 20006
    SIZE_RATIO_SIZE_MISMATCH = 20007

    # ---- 30xxx 裁剪 / 打菲单 ----
    CUTTING_STATUS_NOT_ALLOWED = 30001
    #: 数量冲突。⚠️ ``05 §4`` 原文写「裁剪数量 < 已打菲数量 + 损耗」，
    #: 但 T-CUT-001b 业务确认「行 output_qty 正向录入」（modules/02 C34）后，
    #: **草稿阶段**用它报的是「行 ``output_qty`` < Σ(颜色尺码 output_qty)」
    #: —— 即行余量算出来是负数（C34 的「负数 → 30002」）。两个场景同一个码：
    #: 都是「上层声称的数量装不下下层明细」。
    CUTTING_QTY_CONFLICT = 30002
    CUTTING_QTY_MUST_BE_INTEGER = 30003
    CUTTING_ALREADY_COUNTED = 30004
    CUTTING_REAPPROVAL_REQUIRED = 30005
    #: 手数必须为正整数（ADR-0020：件数 = 手数 × 每手件数，精确值不取整）
    CUTTING_HANDS_MUST_BE_POSITIVE_INT = 30006

    # ---- 31xxx 打菲单 ----
    #: ⚠️ **整段在 T-BUND-004 一次性登记**（docs/05 §4 全部 7 个码，不留缺口）。
    #: 只补 ``31003`` 的话 ``test_implemented_segments_are_fully_implemented`` 会报
    #: 「该段还缺哪些」—— 那条守卫的设计意图正是「登记一个段就把整段补齐」，
    #: 分几批塞只会让守卫失去意义。各码的归属卡见末尾注释。
    #: 打菲号不存在：码格式对但查无此码（扫码输错 / 别厂码；T-BUND-007 + T-PW-001）
    BUNDLE_NO_NOT_FOUND = 31001
    #: 该打菲号已作废（``status='VOIDED'``；T-PW-001 扫码侧）
    BUNDLE_ALREADY_VOIDED = 31002
    #: 打菲件数必须为整数（尾数不生成码，09 §4.2；**T-BUND-004 已用**）
    BUNDLE_QTY_MUST_BE_INTEGER = 31003
    #: 打菲码数与裁剪手数不一致（少打或多打；T-BUND-005a submit 预检 / 005b approve）
    BUNDLE_HANDS_MISMATCH = 31004
    #: 手序号重复（同单同色同尺码同手号已存在；T-BUND-004 预演的 ``conflicts[]`` 预列）
    BUNDLE_HAND_DUPLICATED = 31005
    #: 码已打印，不可静默重建，需作废后重打（ADR-0021；T-BUND-006）
    BUNDLE_ALREADY_PRINTED = 31007

    # ---- 32xxx 计件 ----
    #: ⚠️ **整段随 T-BUND-007b 登记**：打菲侧的反向动作（单码作废 / 反审核）要报
    #: ``32003``（03 §7 + TC-08 / TC-10：``counted_at`` 非空的码不可作废、不可反审核），
    #: 而该码此前在 ``05 §4`` 登记了却**整个 32 段都没进 Python 枚举** —— 于是
    #: 「已计件的码被作废」这条最贵的防线在 P2 之前根本无法表达。
    #: 同段其余五个码仍归 P2 计件模块，守卫名单见
    #: ``tests/test_errors_registry.py::DEFERRED_CODES``。
    #: 计件已结算 / 已计件，不可修改（单码作废与反审核共用这一个判定）
    PIECEWORK_SETTLED = 32003

    # ---- 40xxx 库存 ----
    STOCK_INSUFFICIENT = 40001
    #: 布批可用库存不足（ADR-0022：裁剪领料 > 可用量）
    BATCH_STOCK_INSUFFICIENT = 40006

    # ---- 99xxx ----
    INTERNAL = 99999


#: 错误码 → HTTP 状态码。HTTP 只表达传输层语义，业务含义靠 code 传达（docs/05 §3）。
#: 完整定义见 docs/05 §4 表格的 HTTP 列；此处与该表逐项一致，由单测校验。
HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.OK: HTTPStatus.OK,
    ErrorCode.PARAM_INVALID: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.MISSING_BUSINESS_PARAM: HTTPStatus.BAD_REQUEST,
    ErrorCode.OPTIMISTIC_LOCK_CONFLICT: HTTPStatus.CONFLICT,
    ErrorCode.TOO_MANY_REQUESTS: HTTPStatus.TOO_MANY_REQUESTS,
    ErrorCode.SELF_APPROVAL_FORBIDDEN: HTTPStatus.CONFLICT,
    ErrorCode.REASON_REQUIRED: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.ILLEGAL_STATUS_TRANSITION: HTTPStatus.CONFLICT,
    ErrorCode.ILLEGAL_OPERATION: HTTPStatus.CONFLICT,
    ErrorCode.IMPORT_ROWS_EXCEEDED: HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    ErrorCode.UNAUTHORIZED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.TOKEN_INVALID: HTTPStatus.UNAUTHORIZED,
    ErrorCode.PASSWORD_INCORRECT: HTTPStatus.UNAUTHORIZED,
    ErrorCode.ACCOUNT_DISABLED: HTTPStatus.FORBIDDEN,
    ErrorCode.IMPORT_FILE_FORMAT_ERROR: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.IMPORT_DATA_INVALID: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.EXPORT_RANGE_TOO_LARGE: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.PERMISSION_DENIED: HTTPStatus.FORBIDDEN,
    ErrorCode.DATA_SCOPE_DENIED: HTTPStatus.FORBIDDEN,
    ErrorCode.BASE_DATA_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.STYLE_ALREADY_EXISTS: HTTPStatus.CONFLICT,
    ErrorCode.BASE_DATA_REFERENCED: HTTPStatus.CONFLICT,
    ErrorCode.OPERATION_RATE_NOT_SET: HTTPStatus.NOT_FOUND,
    ErrorCode.RATE_RANGE_OVERLAP: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.SIZE_RATIO_INCOMPLETE: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.SIZE_RATIO_SIZE_MISMATCH: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.CUTTING_STATUS_NOT_ALLOWED: HTTPStatus.CONFLICT,
    ErrorCode.CUTTING_QTY_CONFLICT: HTTPStatus.CONFLICT,
    ErrorCode.CUTTING_QTY_MUST_BE_INTEGER: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.CUTTING_ALREADY_COUNTED: HTTPStatus.CONFLICT,
    ErrorCode.CUTTING_REAPPROVAL_REQUIRED: HTTPStatus.CONFLICT,
    ErrorCode.CUTTING_HANDS_MUST_BE_POSITIVE_INT: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.BUNDLE_NO_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.BUNDLE_ALREADY_VOIDED: HTTPStatus.CONFLICT,
    ErrorCode.BUNDLE_QTY_MUST_BE_INTEGER: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.BUNDLE_HANDS_MISMATCH: HTTPStatus.CONFLICT,
    ErrorCode.BUNDLE_HAND_DUPLICATED: HTTPStatus.CONFLICT,
    ErrorCode.BUNDLE_ALREADY_PRINTED: HTTPStatus.CONFLICT,
    ErrorCode.PIECEWORK_SETTLED: HTTPStatus.CONFLICT,
    ErrorCode.STOCK_INSUFFICIENT: HTTPStatus.CONFLICT,
    ErrorCode.BATCH_STOCK_INSUFFICIENT: HTTPStatus.CONFLICT,
    ErrorCode.INTERNAL: HTTPStatus.INTERNAL_SERVER_ERROR,
}


class BusinessError(Exception):
    """业务异常。**所有**可预期的失败都必须用它抛出。

    :param code: 取自 :class:`ErrorCode`（docs/05 §4 已登记的码）
    :param message: 面向用户的文案，必须说清「为什么 + 下一步」（docs/06 §5）
    :param details: 结构化补充信息，例如冲突区间、被引用的来源清单

    ⚠️ ``message`` 里禁止出现技术细节（异常栈、SQL、表名内部字段），
    也不禁止把密码/token 写进任何日志字段（docs/11 §8.1）。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message: str = message
        self.details: dict[str, Any] = details or {}

    @property
    def http_status(self) -> int:
        """该错误码对应的 HTTP 状态码。"""
        return HTTP_STATUS_BY_CODE[self.code]

    def __repr__(self) -> str:
        return f"BusinessError(code={int(self.code)}, message={self.message!r})"


class NotFoundError(BusinessError):
    """资源不存在。多数场景固定用 ``20001``（基础资料）或各域自己的 not-found 码。"""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.BASE_DATA_NOT_FOUND, message, details)


class PermissionDeniedError(BusinessError):
    """无操作权限（12001）与无数据权限（12002）分开，便于前端区分提示文案。"""

    def __init__(
        self,
        message: str,
        *,
        scope_denied: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            ErrorCode.DATA_SCOPE_DENIED if scope_denied else ErrorCode.PERMISSION_DENIED,
            message,
            details,
        )


class OptimisticLockError(BusinessError):
    """乐观锁冲突（10003）：数据已被他人修改，客户端应刷新后重试。"""

    def __init__(self, message: str = "数据已被他人修改，请刷新后重试") -> None:
        super().__init__(ErrorCode.OPTIMISTIC_LOCK_CONFLICT, message)


class ReasonRequiredError(BusinessError):
    """该动作必须填写原因（10006）：反审核 / 调价 / 停用等。"""

    def __init__(self, message: str = "该操作必须填写原因") -> None:
        super().__init__(ErrorCode.REASON_REQUIRED, message)
