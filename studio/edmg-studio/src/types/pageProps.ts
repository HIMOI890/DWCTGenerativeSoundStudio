export type StudioConfig = any;

export type NavigateFn = (page: any) => void;

export type PageProps = {
  backendUrl: string;
  config: StudioConfig;
  onNavigate?: NavigateFn;
};
