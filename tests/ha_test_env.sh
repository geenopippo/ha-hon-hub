#!/bin/bash
# Fresh fake hOn cloud + fresh throw-away Home Assistant container with this integration mounted.
#   tests/ha_test_env.sh            start (wipes the previous test instance)
#   tests/ha_test_env.sh stop       stop everything and delete the test instance
# Then: python tests/e2e_ha.py setup && (cd tests && python e2e_scenario.py)
# CLOUD_DATA=<folder> serves other appliances (same files as tests/fixtures), CLOUD_I18N=<file> a hOn translation file.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${HA_IMAGE:-ghcr.io/home-assistant/home-assistant:stable}
PY=$ROOT/.venv/bin/python; [ -x "$PY" ] || PY=python3
cd "$ROOT" || exit 1
mkdir -p .ha-test

PID=$(ss -ltnp 2>/dev/null | grep ':8765 ' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | head -1)
[ -n "$PID" ] && kill "$PID" && sleep 0.5
docker rm -f ha-hon-test >/dev/null 2>&1
# the container writes its files as root: remove them from a container too
docker run --rm -v "$ROOT/.ha-test:/t" --entrypoint sh "$IMAGE" -c 'rm -rf /t/config /t/token' 2>/dev/null
[ "$1" = stop ] && { rm -f .ha-test/cloud.log; echo "test environment removed"; exit 0; }

(nohup "$PY" tests/fake_cloud.py --port 8765 ${CLOUD_DATA:+--data "$CLOUD_DATA"} ${CLOUD_I18N:+--i18n "$CLOUD_I18N"} > .ha-test/cloud.log 2>&1 &)
mkdir -p .ha-test/config
cat > .ha-test/config/configuration.yaml <<'YAML'
frontend:
config:
logger:
  default: warning
  logs:
    custom_components.hon_hub: debug
YAML
docker run -d --name ha-hon-test --network host -e TZ=Europe/Rome -e HON_HUB_API_URL=http://127.0.0.1:8765 \
  -e HON_HUB_MQTT_URL=ws://127.0.0.1:8765/mqtt -v "$ROOT/.ha-test/config:/config" \
  -v "$ROOT/custom_components:/config/custom_components:ro" "$IMAGE" | cut -c1-12
for i in $(seq 1 90); do
  c=$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8123/api/onboarding); [ "$c" = "200" ] && break; sleep 2
done
echo "Home Assistant ready (HTTP $c) after $((i*2)) s - http://127.0.0.1:8123"
