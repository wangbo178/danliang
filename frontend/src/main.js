import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/dist/index.css'
import VueECharts from 'vue-echarts'
import { use } from 'echarts/core'
import {
  CanvasRenderer,
} from 'echarts/renderers'
import {
  LineChart,
  BarChart,
  PieChart,
} from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  ToolboxComponent,
  MarkLineComponent,
} from 'echarts/components'

import App from './App.vue'
import router from './router/index.js'

use([
  CanvasRenderer,
  LineChart, BarChart, PieChart,
  GridComponent, TooltipComponent, LegendComponent,
  TitleComponent, DataZoomComponent, ToolboxComponent, MarkLineComponent,
])

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

app.component('v-chart', VueECharts)

app.mount('#app')
