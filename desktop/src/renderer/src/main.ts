import './assets/main.css'

import { createApp } from 'vue'
import App from './App.vue'
import { runtime } from './runtime'

// PU-2: entrance animation, driven by the shell rather than by load.
// Under Electron the window stays hidden until the sidecar is healthy,
// so a load-time CSS animation would finish before anyone could see it;
// in the browser there is nothing to hold back and the runtime fires
// this on the next frame. Either way the app registers the class and
// the shell decides when it lands.
runtime.onReveal(() => {
  document.documentElement.classList.add('af-revealed')
})

createApp(App).mount('#app')
