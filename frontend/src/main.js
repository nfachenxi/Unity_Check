import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import router from './router'
import './assets/global.css'

// Register only the Element Plus icons actually used in the app
// (instead of globally registering all 1400+ icons)
import {
  Odometer, List, FolderOpened, DataAnalysis, Setting,
  Expand, Fold,
  Document, TrendCharts, WarningFilled, HelpFilled,
  Delete, Refresh,
} from '@element-plus/icons-vue'

const app = createApp(App)

// Register only the used icons globally (13 icons instead of 1400+)
const usedIcons = {
  Odometer, List, FolderOpened, DataAnalysis, Setting,
  Expand, Fold,
  Document, TrendCharts, WarningFilled, HelpFilled,
  Delete, Refresh,
}
for (const [key, component] of Object.entries(usedIcons)) {
  app.component(key, component)
}

app.use(ElementPlus, { size: 'default' })
app.use(router)
app.mount('#app')
