# Relays: how the phone reaches Brain

Brain listens on `~/.brain/brain.sock` (owner-only) and, with `--port 8765`, on `127.0.0.1:8765`.
Every HTTP request is authenticated with device-signed sessions, so a shared login node's other users
reaching the loopback port get only 401s — but prefer the socket where your relay supports it.

Pick one. After any hostname change run `brain endpoint set https://<new-host>`; Brain pushes the new
URL to every paired phone, signed with its identity key, so the app switches without a laptop.

## 1. Cloudflare Quick Tunnel — first pairing / testing only
    cloudflared tunnel --url http://127.0.0.1:8765
Needs outbound TCP/UDP 7844 (`brain probe` reports it). Hostname changes on every restart; Quick
Tunnels have no uptime guarantee and `trycloudflare.com` is on some institutional blocklists.

## 2. Cloudflare named tunnel — stable hostname (needs a domain on Cloudflare)
Once, on the login node:
1. `cloudflared tunnel login`
2. `cloudflared tunnel create brain`
3. `cloudflared tunnel route dns brain brain.<your-domain>`
4. Write `~/.brain/tunnel.yml` (keep-alive starts the tunnel whenever this file exists):

        tunnel: brain
        credentials-file: /home/<you>/.cloudflared/<tunnel-id>.json
        ingress:
          - hostname: brain.<your-domain>
            service: http://127.0.0.1:8765   # the port keepalive.sh gives `brain serve`
          - service: http_status:404

5. `brain endpoint set https://brain.<your-domain>`. The phone gets a "Brain moved" push; pair once more at the
   new address, which never changes again.

Keep-alive (cron, `deploy/keepalive.sh`) restarts the tunnel if it dies, even while Brain itself is healthy.
Stop any quick tunnel once the named one works.

Note: Cloudflare terminates TLS at its edge, so it can see traffic in clear. If that is unacceptable,
use option 3.

## 3. SSH from an always-on host you own (rung A shape)
From the host:  `ssh -N -L 8765:/home/<you>/.brain/brain.sock <cluster-login>` and expose 8765 on that
host via Tailscale or a named tunnel there. Key-only SSH works on aqua-hpc (no MFA).

## 4. Tailscale (only if your site allows VPN clients on login nodes)
    tailscaled --tun=userspace-networking --state=$HOME/.tailscale/state --socket=$HOME/.tailscale/sock &
    tailscale --socket=$HOME/.tailscale/sock up && tailscale --socket=$HOME/.tailscale/sock serve 8765
Android runs one VPN at a time; a phone that must hold the university VPN cannot also run Tailscale.
