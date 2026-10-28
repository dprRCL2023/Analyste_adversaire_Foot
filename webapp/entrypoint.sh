#!/bin/sh
# Démarrage du conteneur web : on attend la base, on applique les migrations,
# on collecte les statiques, puis on lance la commande du CMD (gunicorn).
set -e

echo "[entrypoint] migrations (attend la base si besoin)..."
n=0
until python manage.py migrate --noinput; do
  n=$((n + 1))
  if [ "$n" -ge 30 ]; then
    echo "[entrypoint] base injoignable après 30 tentatives — abandon." >&2
    exit 1
  fi
  echo "[entrypoint] base pas prête, nouvel essai dans 2s ($n/30)..."
  sleep 2
done

echo "[entrypoint] collectstatic..."
python manage.py collectstatic --noinput

echo "[entrypoint] démarrage : $*"
exec "$@"
