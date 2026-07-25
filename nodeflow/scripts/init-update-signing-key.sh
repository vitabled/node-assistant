#!/bin/sh
set -eu

project_dir=${1:-.}
pki_dir=$project_dir/pki
private_key=$pki_dir/update-signing.key
public_key=$pki_dir/update-signing.pub

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root so the signing key can be owned by the Panel container group" >&2
  exit 2
fi
if [ -e "$private_key" ] || [ -e "$public_key" ]; then
  if [ ! -f "$private_key" ] || [ ! -f "$public_key" ]; then
    echo "incomplete update-signing keypair; refusing to overwrite it" >&2
    exit 1
  fi
  temporary_public=$(mktemp)
  trap 'rm -f "$temporary_public"' EXIT HUP INT TERM
  openssl pkey -in "$private_key" -pubout -out "$temporary_public"
  if ! cmp -s "$temporary_public" "$public_key"; then
    echo "update-signing public and private keys do not match" >&2
    exit 1
  fi
else
  umask 077
  install -d -m 0750 -o root -g 65532 "$pki_dir"
  openssl genpkey -algorithm ED25519 -out "$private_key"
  openssl pkey -in "$private_key" -pubout -out "$public_key"
fi

chown root:65532 "$private_key" "$public_key"
chmod 0440 "$private_key"
chmod 0444 "$public_key"
printf 'update_public_key_sha256='
openssl pkey -pubin -in "$public_key" -outform DER | sha256sum | cut -d' ' -f1
