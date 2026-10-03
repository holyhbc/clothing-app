import { h } from '../components/h'
import { defineComponent, ref } from 'vue'

import { useToast, ToastHost } from '../components/ui'
import { pageHead } from './shared'

export default defineComponent({
  name: 'LoginPage',
  emits: ['login'],
  setup(_, { emit }) {
    const toast = useToast()
    const no = ref('')
    const pwd = ref('')
    const err = ref('')
    const quick = [
      ['厂长', 'factory_manager'],
      ['车间主管', 'workshop_supervisor'],
      ['仓管', 'warehouse_keeper'],
      ['财务', 'accountant'],
      ['计件员', 'piecework_settler'],
    ]
    return () => [
      h('div', { style: 'min-height:100vh' }, [
        h('div', { class: 'login-wrap' }, [
          h('div', { class: 'login-hero' }, [
            h('div', {}, [
              h('div', { style: 'display:flex;align-items:center;gap:10px;margin-bottom:28px' }, [
                h('div', { style: 'width:34px;height:34px;border-radius:8px;background:rgba(255,255,255,.2);display:grid;place-items:center;font-size:18px' }, '衣'),
                h('span', { style: 'font-size:18px;font-weight:600' }, '服装厂 ERP'),
              ]),
              h('h1', {}, '从裁剪到收款，一套系统走完'),
              h('div', { class: 'lead' }, '裁剪 · 打菲 · 扫码计件 · 计件工资 · 物料与成衣库存 · 销售 · 应收应付 · 记账凭证。单据全留痕，审核与反审核成对出现，工资永远算得清。'),
              h('div', { class: 'feat' }, [
                '一衣一码', '扫码计件', '批次成本', '整件口径', '权限到人', '操作留痕',
              ].map((x) => h('span', {}, x))),
            ]),
            h('div', { style: 'color:rgba(255,255,255,.7);font-size:12px' }, '原型演示页 · 无后端交互 · 数据为模拟数据'),
          ]),
          h('div', { class: 'login-form' }, [
            h('div', { class: 'login-box' }, [
              h('h2', {}, '登录管理端'),
              h('div', { class: 'sub' }, '工号 / 员工编号 + 密码'),
              h('div', { class: 'field-block' }, [
                h('label', {}, '工号'),
                h('input', { class: 'inp', placeholder: '如 admin / A001', value: no.value, onInput: (e: Event) => { no.value = (e.target as HTMLInputElement).value; err.value = '' } }),
              ]),
              h('div', { class: 'field-block' }, [
                h('label', {}, '密码'),
                h('input', { class: 'inp', type: 'password', placeholder: '首次登录需改密', value: pwd.value, onInput: (e: Event) => (pwd.value = (e.target as HTMLInputElement).value) }),
                err.value ? h('div', { class: 'form-err' }, err.value) : null,
              ]),
              h('button', {
                class: 'btn primary lg',
                onClick: () => {
                  if (!no.value.trim()) { err.value = '请输入工号'; return }
                  if (pwd.value.length < 8) { err.value = '密码至少 8 位（含字母与数字）；连续 5 次失败锁定 15 分钟'; return }
                  toast.ok('登录成功（演示）'); emit('login')
                },
              }, '登 录'),
              h('div', { class: 'login-roles' }, [
                '演示角色（点击直接进入）：',
                ...quick.map(([label]) => h('a', { onClick: () => { toast.ok('以 ' + label + ' 身份登录（演示）'); emit('login') } }, label)),
              ]),
              h('div', { style: 'margin-top:18px;padding-top:16px;border-top:1px dashed var(--color-border);font-size:12px;color:var(--color-text-third);line-height:1.9' }, [
                '员工端请用手机打开 H5（手机号 + 短信验证码），',
                h('br'),
                h('a', { style: 'color:var(--color-primary)', href: '#/mobile' }, '预览员工端 H5 ›'),
                '　｜　认证方式后续可切换微信/企业微信（AuthProvider 已预留）',
              ]),
            ]),
          ]),
        ]),
      ]),
      h(ToastHost, { items: toast.items }),
    ]
  },
})