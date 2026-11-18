/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_BASE?: string;
  readonly VITE_MODE?: "auto" | "demo" | "live";
  readonly VITE_API_BASE?: string;
}
