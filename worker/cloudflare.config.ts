// Configuratie voor de Cloudflare Worker (tussenschakel). Uitrollen met: cf deploy
import { defineConfig } from "cf/config";

import * as entrypoint from "./worker.js" with { type: "cf-worker" };

export default defineConfig({
  worker: {
    name: "iptv-cast",
    entrypoint,
    compatibilityDate: "2026-11-01",
    workersDev: true,
  },
});
