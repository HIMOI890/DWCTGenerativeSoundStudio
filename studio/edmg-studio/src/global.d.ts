export {};

declare global {
  interface Window {
    edmg?: {
      backendUrl: () => string;
      getBackendUrl?: () => Promise<string>;
      openExternal?: (url: string) => Promise<void>;
      openPath?: (path: string) => Promise<{ ok: boolean; action?: string; path?: string; error?: string }>;
      showItemInFolder?: (path: string) => Promise<{ ok: boolean; action?: string; path?: string; error?: string }>;
      revealPath?: (path: string) => Promise<{ ok: boolean; action?: string; path?: string; error?: string }>;
      pickFile?: (opts?: any) => Promise<{ ok: boolean; canceled?: boolean; paths?: string[] }>;
      pickDirectory?: (opts?: any) => Promise<{ ok: boolean; canceled?: boolean; path?: string }>;
      getStudioPaths?: () => Promise<{
        ok: boolean;
        studioHome: string;
        dataDir: string;
        cacheRoot: string;
        electronUserData: string;
        sessionData: string;
        logsDir: string;
        bootstrapConfigPath: string;
        pendingMigration?: any;
        lastMigration?: any;
        source: string;
      }>;
      setStudioHome?: (path: string) => Promise<{
        ok: boolean;
        error?: string;
        restartRequired?: boolean;
        migrationPlanned?: boolean;
        migrationSummary?: string;
        studioHome?: string;
        dataDir?: string;
        cacheRoot?: string;
      }>;
      relaunch?: () => Promise<{ ok: boolean }>;
    };
    __EDMG_BACKEND_URL__?: string;
  }
}
