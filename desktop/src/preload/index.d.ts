import { ElectronAPI } from '@electron-toolkit/preload'

export interface SidecarInfo {
  port: number
  token: string
  url: string
}

export interface AlgoForgeAPI {
  sidecar: {
    getInfo: () => Promise<SidecarInfo | null>
  }
}

declare global {
  interface Window {
    electron: ElectronAPI
    algoforge: AlgoForgeAPI
  }
}
