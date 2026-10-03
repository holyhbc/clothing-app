import { h } from './components/h'
import { defineComponent, ref, computed, type Component } from 'vue'

import PcShell from './pc/Shell'
import LoginPage from './pc/Login'
import MobileApp from './mobile/MobileApp'
import Dashboard from './pc/pages/Dashboard'
import Cutting from './pc/pages/Cutting'
import Bundling from './pc/pages/Bundling'
import Station from './pc/pages/Station'
import PieceworkPage, { PayrollPage } from './pc/pages/Piecework'
import Rates from './pc/pages/Rates'
import { StockPage, SalesPage, ArPage, VoucherPage, PermPage } from './pc/pages/Business'
import { ProductPage, BaseDataPage } from './pc/pages/Product'
import { StockOpsPage, SizeRatioPage } from './pc/pages/Operations'
import { PurchasePage } from './pc/pages/Operations2'
import { StationHand } from './pc/pages/Hands'
import { LedgerPage } from './pc/pages/Ledger'
import { TracePage } from './pc/pages/Trace'
import { CuttingMatrixPage } from './pc/pages/CuttingMatrix'

const REGISTRY: Record<string, Component> = {
  dashboard: Dashboard,
  product: ProductPage,
  base: BaseDataPage,
  purchase: PurchasePage,
  stockops: StockOpsPage,
  sizeratio: SizeRatioPage,
  ledger: LedgerPage,
  trace: TracePage,
  cutting: Cutting,
  cuttingmatrix: CuttingMatrixPage,
  bundling: Bundling,
  station: StationHand,
  piecework: PieceworkPage,
  payroll: PayrollPage,
  rates: Rates,
  stock: StockPage,
  sales: SalesPage,
  ar: ArPage,
  voucher: VoucherPage,
  perm: PermPage,
}

export default defineComponent({
  name: 'App',
  setup() {
    const route = ref(window.location.hash.replace(/^#\/?/, '') || 'login')
    const logged = ref(route.value !== 'login' && route.value !== 'mobile')

    window.addEventListener('hashchange', () => {
      const r = window.location.hash.replace(/^#\/?/, '') || 'login'
      route.value = r
      if (r === 'login') logged.value = false
      if (r === 'mobile') logged.value = true
      if (r.startsWith('pc/')) logged.value = true
    })

    function go(key: string) {
      window.location.hash = '/pc/' + key
    }
    function doLogin() {
      logged.value = true
      go('dashboard')
    }

    const key = computed(() => (route.value.startsWith('pc/') ? route.value.slice(3) : route.value))
    const current = computed<Component>(() => REGISTRY[key.value] ?? Dashboard)

    return () => {
      if (route.value === 'mobile') return h(MobileApp)
      if (!logged.value || route.value === 'login') return h(LoginPage, { onLogin: doLogin })
      return h(PcShell, { active: key.value, onNavigate: go }, {
        body: () => h(current.value, { onNavigate: go }),
      })
    }
  },
})