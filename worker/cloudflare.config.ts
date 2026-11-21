// Configuratie voor de Cloudflare Worker (tussenschakel). Uitrollen met: cf deploy --secrets-file secrets.env
import { defineConfig, bindings } from "cf/config";

import * as entrypoint from "./worker.js" with { type: "cf-worker" };

export default defineConfig({
  worker: {
    name: "iptv-cast",
    entrypoint,
    compatibilityDate: "2026-11-01",
    workersDev: true,
    env: {
      // Workers VPC: via de Cloudflare Tunnel naar de Beelink (poort 8030), tunnel "iptv-beelink"
      HOME: bindings.vpcService({ id: "01a1fe83-c373-7312-abb1-ecbfdd476e47" }),
    },
  },
});
