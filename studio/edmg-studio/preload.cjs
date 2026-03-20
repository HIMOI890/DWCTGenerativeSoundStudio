const { contextBridge, ipcRenderer, shell } = require("electron");

function getArgValue(prefix) {
  const found = process.argv.find((entry) => typeof entry === "string" && entry.startsWith(prefix));
  return found ? found.slice(prefix.length) : "";
}

const BACKEND_HOST =
  process.env.EDMG_STUDIO_BACKEND_HOST ||
  getArgValue("--edmg-backend-host=") ||
  "127.0.0.1";

const BACKEND_PORT =
  process.env.EDMG_STUDIO_BACKEND_PORT ||
  getArgValue("--edmg-backend-port=") ||
  "7863";

const DEFAULT_BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

contextBridge.exposeInMainWorld("edmg", {
  backendUrl: () => DEFAULT_BACKEND_URL,

  getBackendUrl: async () => {
    try {
      const url = await ipcRenderer.invoke("edmg:getBackendUrl");
      if (typeof url === "string" && url.trim()) {
        return url;
      }
    } catch {}

    return DEFAULT_BACKEND_URL;
  },

  openExternal: (url) => shell.openExternal(String(url)),
  openPath: (targetPath) => ipcRenderer.invoke("edmg:openPath", targetPath),
  showItemInFolder: (targetPath) => ipcRenderer.invoke("edmg:revealPath", targetPath),
  revealPath: (targetPath) => ipcRenderer.invoke("edmg:revealPath", targetPath),
  pickFile: (options) => ipcRenderer.invoke("edmg:pickFile", options),
  pickDirectory: (options) => ipcRenderer.invoke("edmg:pickDirectory", options),
  getStudioPaths: () => ipcRenderer.invoke("edmg:getStudioPaths"),
  getAiSettings: () => ipcRenderer.invoke("edmg:getAiSettings"),
  setStudioHome: (targetPath) => ipcRenderer.invoke("edmg:setStudioHome", targetPath),
  setStorageSettings: (settings) => ipcRenderer.invoke("edmg:setStorageSettings", settings),
  setAiSettings: (settings) => ipcRenderer.invoke("edmg:setAiSettings", settings),
  relaunch: () => ipcRenderer.invoke("edmg:relaunch"),
});

if ((process.env.EDMG_STUDIO_TEST_MODE ?? "0") === "1") {
  contextBridge.exposeInMainWorld("__edmgTest", {
    writeReport: (payload) => ipcRenderer.invoke("edmg:testWriteReport", payload),
  });
}
