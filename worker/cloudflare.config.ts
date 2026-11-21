// Configuratie voor de Cloudflare Worker (tussenschakel).
//
// Productie (standaard):   cf deploy --secrets-file secrets.env
//   -> Worker "iptv-cast", Beelink-poort 8030
// Ontwikkeling (dev):      $env:IPTV_ENV = 'dev'; cf deploy --secrets-file secrets.env   (vanuit de map iptv-cast-dev)
//   -> Worker "iptv-cast-dev", Beelink-poort 8031
import { defineConfig, bindings } from "cf/config";

import * as entrypoint from "./worker.js" with { type: "cf-worker" };

const DEV = process.env.IPTV_ENV === "dev";

export default defineConfig({
  worker: {
    name: DEV ? "iptv-cast-dev" : "iptv-cast",
    entrypoint,
    compatibilityDate: "2026-11-01",
    workersDev: true,
    env: {
      // Workers VPC: via de Cloudflare Tunnel "iptv-beelink" naar de Beelink
      HOME: bindings.vpcService({
        id: DEV
          ? "01a1feaf-e2de-7392-a0f3-02fc2523e2aa"   // iptv-beelink-dev  -> 127.0.0.1:8031
          : "01a1fe83-c373-7312-abb1-ecbfdd476e47",  // iptv-beelink      -> 127.0.0.1:8030
      }),
    },
  },
});
