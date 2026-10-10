/**
 * `bwip-js` 的**局部环境声明**。
 *
 * ## 为什么需要它（而不是「types 装不上就换个库」）
 *
 * `bwip-js@4.11` **自带**类型（`dist/bwip-js.d.ts` 确实存在），但它是通过
 * `package.json` 的 **`exports` 映射**暴露的，而那个映射的条件是
 * `browser` / `electron` / `react-native` / `node` —— **没有一条 `default` 兜底**。
 *
 * 本仓 `tsconfig.base.json` 用的是 `moduleResolution: "bundler"`，但 vue-tsc 对
 * `.ts` 源文件按 **CJS 模式**解析（条件集是 `import` + `types`），于是上面四条
 * **一条都不匹配** → `TS2307: Cannot find module 'bwip-js'`。
 *
 * ⚠️ **不要装 `@types/bwip-js` 试图绕过**：那个包是 npm 上的**废弃 stub**
 * （`"deprecated": "bwip-js provides its own types"`、`"main": ""`、包内无
 * `index.d.ts`），装上之后 `main: ""` 会让解析**更早失败**，错误信息还更费解。
 * 实测：装它 → 同样 TS2307；删它 → 仍然 TS2307（但至少不再遮蔽包自带类型）。
 *
 * ## 为什么在这里声明而不是改 tsconfig
 *
 * 改 `moduleResolution` 或加 `customConditions: ["browser"]` 是**全局**改动，
 * 会影响每一个包的模块解析 —— 为一个库去动全仓编译语义，代价与风险都远大于收益。
 * 局部声明把影响面收在这一个 import 上。
 *
 * ## 范围
 *
 * **只声明实际用到的一个函数**（`toDataURL`）。刻意不做完整声明：
 * 完整声明等于把第三方类型抄一份进本仓，第三方升级时它不会跟着变 ——
 * 那就是第二份真相，而 `docs/06 §7` 明确禁止手写 DTO。
 * 真要用到别的 API 时按需加一行，比先抄 300 行安全。
 */
declare module 'bwip-js' {
  /** 生成条码图片的 DataURL。参数形状与官方 `toDataURL(opts)` 一致。 */
  export function toDataURL(options: Record<string, unknown>): Promise<string>
}
