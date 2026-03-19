const { contextBridge, ipcRenderer } = require('electron');

// Expose minimal config to the renderer.
contextBridge.exposeInMainWorld('EDMG', {
  apiBase: () => {
    const host = process.env.EDMG_API_HOST || '127.0.0.1';
    const port = process.env.EDMG_API_PORT || '7862';
    return `http://${host}:${port}`;
  },
  saveJson: (defaultName, content) => ipcRenderer.invoke('edmg-save-json', defaultName, content),
  openJson: () => ipcRenderer.invoke('edmg-open-json'),
  openPath: (relativePath, reveal) => ipcRenderer.invoke('edmg-open-path', relativePath, reveal),
  restartApi: () => ipcRenderer.invoke('edmg-restart-api')
});
