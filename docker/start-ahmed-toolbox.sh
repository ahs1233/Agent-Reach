#!/bin/sh
set -eu

POT_PORT="${YTDLP_POT_PROVIDER_PORT:-4416}"
export YTDLP_POT_PROVIDER_URL="${YTDLP_POT_PROVIDER_URL:-http://127.0.0.1:${POT_PORT}}"

PORT="${POT_PORT}" HOST=127.0.0.1 node /opt/bgutil-pot/server/build/main.js &
pot_pid=$!

cleanup() {
  kill "${pot_pid}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Bound startup wait: do not start the public app with a silently dead provider.
attempt=0
until curl -fsS "${YTDLP_POT_PROVIDER_URL}/ping" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge 30 ]; then
    echo "PO token provider failed to become healthy" >&2
    exit 1
  fi
  sleep 1
done

if [ "${AHMED_YOUTUBEJS_LIVE_ACCEPTANCE:-0}" = "1" ]; then
  echo "Running independent YouTube.js acquisition acceptance..." >&2
  node --input-type=module -e 'import { Innertube, UniversalCache } from "youtubei.js"; const id=process.env.AHMED_VIDEO_ID||"9Ignyhh1WqQ"; const clients=["TV_EMBEDDED","ANDROID_VR","WEB_EMBEDDED","TV"]; let errors=[]; for (const client_name of clients) { try { const y=await Innertube.create({cache:new UniversalCache(false),client_name}); const i=await y.getInfo(id); const d=await i.download({type:"audio",quality:"best",client:client_name}); const r=d.getReader(); let n=0; while(n<262144){const x=await r.read(); if(x.done) break; n+=x.value.byteLength;} if(n>0){console.log("AHMED_YOUTUBEJS_BENCHMARK="+JSON.stringify({video_id:id,client:client_name,media_bytes_sampled:n,title:i.basic_info?.title||null})); process.exit(0);} } catch(e) { errors.push({client:client_name,error:String(e?.message||e)}); console.error("YouTube.js client failed",client_name,String(e?.message||e)); } } throw new Error("All YouTube.js clients failed: "+JSON.stringify(errors));'
fi

if [ "${AHMED_VIDEO_LIVE_ACCEPTANCE:-0}" = "1" ]; then
  echo "Running Ahmed Video Intelligence live acceptance..." >&2
  /opt/venv/bin/python -m agent_reach.toolbox.video_live_acceptance
fi

exec ahmed-toolbox
