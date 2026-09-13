import './assets/main.css'

import { createApp } from 'vue'
import App from './App.vue'

// PU-2: entrance animation, driven by the main process rather than by
// load. The window stays hidden until the sidecar is healthy, so a
// load-time CSS animation would finish before the user could see it.
// `onReveal` is optional so the renderer still works if it is loaded
// outside Electron (useful for browser-based UI work). Without the
// class the app is simply visible with no animation — safe default.
window.algoforge?.app?.onReveal(() => {
  document.documentElement.classList.add('af-revealed')
})

createApp(App).mount('#app')
