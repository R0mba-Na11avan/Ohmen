import { contextBridge, ipcRenderer } from 'electron'

// The renderer never sees Node or the sidecar directly — only this tiny surface.
// API keys go in (saveProvider) but never come back out.
contextBridge.exposeInMainWorld('pcb', {
  getPort: (): Promise<number> => ipcRenderer.invoke('get-port'),
  getToken: (): Promise<string> => ipcRenderer.invoke('get-token'),
  sidecarReady: (): Promise<boolean> => ipcRenderer.invoke('sidecar-ready'),
  pickProject: (): Promise<string | null> => ipcRenderer.invoke('pick-project'),
  newProject: (): Promise<string | null> => ipcRenderer.invoke('new-project'),
  revealProject: (source: string): Promise<string> => ipcRenderer.invoke('project:reveal', source),

  savedProviders: () => ipcRenderer.invoke('providers:saved'),
  saveProvider: (provider: string, input: unknown) => ipcRenderer.invoke('providers:save', provider, input),
  setLocalOverrides: (overrides: unknown) => ipcRenderer.invoke('providers:overrides', overrides),
  removeProvider: (provider: string) => ipcRenderer.invoke('providers:remove', provider),
  savedServices: () => ipcRenderer.invoke('services:saved'),
  saveService: (service: string, input: unknown) => ipcRenderer.invoke('services:save', service, input),
  removeService: (service: string) => ipcRenderer.invoke('services:remove', service),
  getEngines: () => ipcRenderer.invoke('engines:get'),
  saveEngines: (settings: unknown) => ipcRenderer.invoke('engines:save', settings),
  pickEngineCli: (name: string): Promise<string | null> => ipcRenderer.invoke('engines:pick', name),
  setKicadCli: (path: string) => ipcRenderer.invoke('kicad:set', path),
  pickKicadCli: (): Promise<string | null> => ipcRenderer.invoke('kicad:pick'),
  appInfo: () => ipcRenderer.invoke('app:info'),
  getSettings: () => ipcRenderer.invoke('settings:get'),
  setSettings: (partial: unknown) => ipcRenderer.invoke('settings:set', partial),
})
