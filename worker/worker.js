// IPTV Cast — tussenschakel (Cloudflare Worker)
//
// Waarom? De app draait op https (GitHub Pages), je IPTV-server op http.
// Een https-pagina mag geen http-data ophalen, en de Chromecast eist bij live-tv (HLS)
// en ondertitels speciale "CORS"-headers die IPTV-servers meestal niet sturen.
// Deze Worker haalt alles op namens de app en zet de juiste headers erbij.
//
// Endpoints (allemaal met ?k=<APP_KEY>):
//   /api          -> player_api.php van je Xtream-server (lijsten, info)
//   /m3u8?url=    -> HLS-afspeellijst, met alle segment-links herschreven via /p
//   /p?url=       -> rechtstreeks doorgeven (videosegmenten, mp4, afbeeldingen)
//   /subs/search  -> ondertitels zoeken op OpenSubtitles
//   /subs/file?id -> één ondertitel downloaden als WebVTT (formaat voor Chromecast)
//
// Instellingen (Cloudflare > Worker > Settings > Variables and Secrets):
//   APP_KEY      (verplicht) zelfgekozen geheime code, dezelfde als in de app
//   OS_API_KEY   (optioneel) API-sleutel van opensubtitles.com
//   OS_USERNAME  (optioneel) je OpenSubtitles-gebruikersnaam (meer downloads per dag)
//   OS_PASSWORD  (optioneel) je OpenSubtitles-wachtwoord
//
// Binding HOME (Workers VPC, via een Cloudflare Tunnel naar de Beelink):
//   lijsten en afbeeldingen gaan eerst via de Beelink (met VPN), want je provider
//   weigert Cloudflare af en toe. Lukt dat niet, dan rechtstreeks zoals vroeger.

const IPTV_UA = 'VLC/3.0.20 LibVLC/3.0.20'; // veel IPTV-servers accepteren alleen bekende spelers
const OS_UA = 'IPTVCast v1.0';
const OS_BASE = 'https://api.opensubtitles.com/api/v1';

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === 'OPTIONS') return cors(new Response(null, { status: 204 }));
    if (url.pathname === '/') return cors(json({ ok: true, app: 'iptv-cast' }));

    if (!env.APP_KEY) return cors(json({ error: 'APP_KEY is niet ingesteld in de Worker' }, 500));
    if (url.searchParams.get('k') !== env.APP_KEY) return cors(json({ error: 'Verkeerde sleutel' }, 403));

    try {
      switch (url.pathname) {
        case '/api': return cors(await viaHome(env, url, '/api') || await api(url));
        case '/m3u8': return cors(await viaHome(env, url, '/m3u8', null, { base: url.origin }) || await playlist(url));
        case '/p': return cors(await viaHome(env, url, '/p', req) || await pass(url, req));
        case '/subs/search': return cors(await subsSearch(url, env));
        case '/subs/file': return cors(await subsFile(url, env));
      }
      return cors(json({ error: 'Onbekend adres' }, 404));
    } catch (e) {
      return cors(json({ error: String(e && e.message || e) }, 502));
    }
  },
};

// ---------- Via de Beelink (tunnel) ----------

// Geeft het antwoord van de Beelink, of null als die niet bereikbaar is of de VPN uit staat.
async function viaHome(env, url, path, req, extra) {
  if (!env.HOME) return null;
  try {
    const headers = {};
    const range = req && req.headers.get('Range');
    if (range) headers.Range = range;
    // host en poort hier worden genegeerd: de VPC-service bepaalt waar het heen gaat
    const qs = new URLSearchParams(url.searchParams);
    for (const [k, v] of Object.entries(extra || {})) qs.set(k, v);
    const r = await env.HOME.fetch(`http://localhost:8030${path}?${qs}`, { headers });
    if (r.status === 503 || r.status === 502) return null; // VPN uit of server gaf geen antwoord
    const out = new Headers();
    for (const n of ['content-type', 'content-length', 'content-range', 'accept-ranges']) {
      const v = r.headers.get(n);
      if (v) out.set(n, v);
    }
    out.set('Cache-Control', 'no-store');
    out.set('X-Via', 'beelink');
    return new Response(r.body, { status: r.status, headers: out });
  } catch (e) {
    return null; // Beelink of tunnel niet bereikbaar
  }
}

// ---------- Xtream ----------

async function api(url) {
  const server = (url.searchParams.get('s') || '').replace(/\/+$/, '');
  if (!/^https?:\/\//.test(server)) return json({ error: 'Ongeldig serveradres' }, 400);
  const qs = new URLSearchParams(url.searchParams);
  qs.delete('s'); qs.delete('k');
  const r = await fetch(`${server}/player_api.php?${qs}`, { headers: { 'User-Agent': IPTV_UA } });
  const body = await r.text();
  return new Response(body, {
    status: r.status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

function target(url) {
  const t = url.searchParams.get('url') || '';
  if (!/^https?:\/\//.test(t)) throw new Error('Ongeldige url');
  return t;
}

// Haalt een HLS-afspeellijst op en laat elke link erin ook via deze Worker lopen.
async function playlist(url) {
  const r = await fetch(target(url), { headers: { 'User-Agent': IPTV_UA }, redirect: 'follow' });
  if (!r.ok) return json({ error: `Server gaf fout ${r.status}` }, 502);
  const base = r.url; // na eventuele doorverwijzing naar een andere server
  const text = await r.text();
  if (!text.trimStart().startsWith('#EXTM3U')) return json({ error: 'Geen geldige HLS-afspeellijst' }, 502);

  const self = `${url.origin}`;
  const k = url.searchParams.get('k');
  const wrap = (link) => {
    const abs = new URL(link, base).href;
    const path = /\.m3u8($|\?)/i.test(new URL(abs).pathname) ? '/m3u8' : '/p';
    return `${self}${path}?k=${encodeURIComponent(k)}&url=${encodeURIComponent(abs)}`;
  };
  const out = text.split(/\r?\n/).map((line) => {
    const t = line.trim();
    if (!t) return line;
    if (t.startsWith('#')) return line.replace(/URI="([^"]+)"/g, (_, u) => `URI="${wrap(u)}"`);
    return wrap(t);
  }).join('\n');

  return new Response(out, {
    headers: { 'Content-Type': 'application/vnd.apple.mpegurl', 'Cache-Control': 'no-store' },
  });
}

// Geeft een bestand ongewijzigd door (met ondersteuning voor vooruit/terugspoelen).
async function pass(url, req) {
  const t = target(url);
  const headers = { 'User-Agent': IPTV_UA };
  const range = req.headers.get('Range');
  if (range) headers.Range = range;
  const r = await fetch(t, { headers, redirect: 'follow' });
  const out = new Headers();
  for (const n of ['content-type', 'content-length', 'content-range', 'accept-ranges', 'last-modified']) {
    const v = r.headers.get(n);
    if (v) out.set(n, v);
  }
  if (/\.ts($|\?)/i.test(new URL(t).pathname)) out.set('content-type', 'video/mp2t');
  return new Response(r.body, { status: r.status, headers: out });
}

// ---------- OpenSubtitles ----------

let osToken = null;      // blijft bewaard zolang deze Worker-instantie leeft
let osBase = OS_BASE;
const vttMemo = new Map();

async function os(env, path, init = {}, useLogin = false) {
  if (!env.OS_API_KEY) throw new Error('OpenSubtitles is niet ingesteld (OS_API_KEY ontbreekt)');
  const headers = {
    'Api-Key': env.OS_API_KEY,
    'User-Agent': OS_UA,
    'Accept': 'application/json',
    'Content-Type': 'application/json',
  };
  let base = OS_BASE;
  if (useLogin && env.OS_USERNAME && env.OS_PASSWORD) {
    if (!osToken) {
      const lr = await fetch(`${OS_BASE}/login`, {
        method: 'POST', headers,
        body: JSON.stringify({ username: env.OS_USERNAME, password: env.OS_PASSWORD }),
      });
      const ld = await lr.json().catch(() => ({}));
      if (ld.token) {
        osToken = ld.token;
        if (ld.base_url) osBase = `https://${ld.base_url}/api/v1`;
      }
    }
    if (osToken) { headers.Authorization = `Bearer ${osToken}`; base = osBase; }
  }
  return fetch(base + path, { ...init, headers });
}

async function subsSearch(url, env) {
  const p = url.searchParams;
  const langs = (p.get('lang') || 'nl,en').toLowerCase().split(',').map((s) => s.trim()).filter(Boolean).sort().join(',');
  const isEpisode = !!p.get('season');

  const attempt = async (useTmdb) => {
    const q = new URLSearchParams({ languages: langs });
    if (isEpisode) {
      if (useTmdb) q.set('parent_tmdb_id', p.get('tmdb')); else q.set('query', p.get('query'));
      q.set('season_number', p.get('season'));
      q.set('episode_number', p.get('episode'));
      q.set('type', 'episode');
    } else {
      if (useTmdb) q.set('tmdb_id', p.get('tmdb'));
      else { q.set('query', p.get('query')); if (p.get('year')) q.set('year', p.get('year')); }
      q.set('type', 'movie');
    }
    q.sort(); // OpenSubtitles wil de parameters alfabetisch
    const r = await os(env, `/subtitles?${q}`);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.message || `OpenSubtitles gaf fout ${r.status}`);
    return d.data || [];
  };

  let data = [];
  if (p.get('tmdb') && /^\d+$/.test(p.get('tmdb'))) data = await attempt(true);
  if (!data.length && p.get('query')) data = await attempt(false);

  const list = data.flatMap((x) => {
    const a = x.attributes || {};
    const f = (a.files || [])[0];
    if (!f) return [];
    return [{
      id: f.file_id,
      lang: a.language || '',
      release: a.release || f.file_name || '',
      downloads: a.download_count || 0,
      hi: !!a.hearing_impaired,
    }];
  });
  return json(list);
}

async function subsFile(url, env) {
  const id = url.searchParams.get('id');
  if (!/^\d+$/.test(id || '')) return json({ error: 'Ongeldige ondertitel' }, 400);
  if (!vttMemo.has(id)) {
    const r = await os(env, '/download', {
      method: 'POST',
      body: JSON.stringify({ file_id: Number(id), sub_format: 'webvtt' }),
    }, true);
    const d = await r.json().catch(() => ({}));
    if (!d.link) throw new Error(d.message || `Download mislukt (${r.status})`);
    const text = await (await fetch(d.link)).text();
    vttMemo.set(id, toVtt(text));
    if (vttMemo.size > 50) vttMemo.delete(vttMemo.keys().next().value);
  }
  return new Response(vttMemo.get(id), {
    headers: { 'Content-Type': 'text/vtt; charset=utf-8', 'Cache-Control': 'public, max-age=86400' },
  });
}

// Zet SRT om naar WebVTT (als het niet al WebVTT is).
function toVtt(text) {
  let t = text.replace(/^﻿/, '').replace(/\r\n?/g, '\n').trim();
  if (t.startsWith('WEBVTT')) return t + '\n';
  t = t.replace(/(\d{1,2}:\d{2}:\d{2}),(\d{1,3})/g, '$1.$2');
  return 'WEBVTT\n\n' + t + '\n';
}

// ---------- hulpjes ----------

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

function cors(res) {
  const r = new Response(res.body, res);
  r.headers.set('Access-Control-Allow-Origin', '*');
  r.headers.set('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS');
  r.headers.set('Access-Control-Allow-Headers', '*');
  r.headers.set('Access-Control-Expose-Headers', 'Content-Length, Content-Range, Accept-Ranges');
  return r;
}
