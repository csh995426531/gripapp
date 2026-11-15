# Embedded `wireguard-go`

Grip ships platform builds of [wireguard-go](https://git.zx2c4.com/wireguard-go) so
operators and enrolled Nodes can use **WireGuard direct** without installing
system `wireguard-tools` / `wg-quick`.

Rebuild (requires Go):

```bash
git clone --depth 1 https://git.zx2c4.com/wireguard-go /tmp/wg-src
cd /tmp/wg-src
LDFLAGS='-s -w -X golang.zx2c4.com/wireguard/ipc.socketDirectory=/tmp/grip-wireguard'
for pair in darwin/arm64 darwin/amd64 linux/amd64 linux/arm64; do
  os=${pair%/*}; arch=${pair#*/}
  CGO_ENABLED=0 GOOS=$os GOARCH=$arch go build -ldflags "$LDFLAGS" \
    -o "$OLDPWD/wireguard-go-${os}-${arch}" .
done
```

UAPI sockets live under `/tmp/grip-wireguard/` (user-writable).
