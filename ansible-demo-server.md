# Ansible demo-server provisioning — handoff notes

## Goal

Spin up a fresh, browser-accessible VisLang demo environment on AWS in one
command, so domain scientists can "drive" a demo from their own laptop's
browser without installing anything. Teardown should be equally one-command.

Use case is occasional (weekly-ish) demos, not production. Instance lives
briefly, gets terminated afterward.

## Decided architecture

- **One Ansible playbook** does everything: AWS provisioning *and* in-instance
  config. Not Terraform + Ansible. Rationale: disposable single-VM scale doesn't
  benefit from Terraform's state management, and one tool is simpler.
- **Companion teardown playbook** (`terminate.yml`) cleans up the instance.
  Leave the SG, IAM role/profile, and Route 53 record intact across runs.

## AWS choices

- **Region: us-east-2.** New accounts hit instance-family filtering in us-east-1
  that doesn't happen in us-east-2.
- **Instance: m7i.2xlarge** (8 vCPU, 32 GB). CPU-only because the G-family
  quota request didn't come through in time. llvmpipe software rendering is
  adequate for the conversational workflow; poor for mouse-drag camera orbit.
  If a GPU quota becomes available later, swap to g4dn.xlarge or g5.xlarge.
- **AMI: Ubuntu 24.04 LTS (noble), x86_64.**
- **IAM instance profile** with `route53:ChangeResourceRecordSets`,
  `route53:GetChange`, `route53:ListHostedZones` on the single hosted zone.
  Used by both the Route 53 A-record update script and Caddy's DNS-01 challenge.

## DNS + TLS

- Route 53 hosted zone already exists (user owns the domain).
- A record (e.g. `vislang-demo.<domain>`) updated on each instance boot by a
  systemd oneshot that hits IMDSv2 for the public IP and calls
  `route53 change-resource-record-sets`. TTL 60.
- **TLS via Caddy reverse proxy on :443** → KasmVNC on localhost:8443.
  Caddy auto-provisions Let's Encrypt certs via DNS-01 (Route 53 plugin),
  handles WebSocket upgrade, auto-renews. No certbot. No port in the URL.
- Let's Encrypt rate limits are irrelevant at our frequency.

## Remote desktop stack

- **KasmVNC** (not TigerVNC + noVNC). Better frame rate on 3D content, built-in
  shared sessions, dynamic resolution. Installed from the official `.deb` on
  GitHub releases — no apt repo exists for Kasm despite some docs suggesting
  otherwise.
- **XFCE** as the desktop. Lightweight, works fine under llvmpipe.
- Multiple concurrent browser connections to the same session work out of the
  box (presenter + scientist both see the same desktop, both can input).

## In-instance software

- Node.js 20 (NodeSource), `@anthropic-ai/claude-code` via npm
- `gh` CLI for private-repo clone (device flow auth, pasted on laptop)
- VS Code (snap is fine) with the Claude Code extension
- `xdg-utils` (critical — see gotchas)
- A browser for the Claude OAuth flow. Snap Firefox works fine under the VNC
  as long as `xdg-utils` is installed first; no need for a non-snap alternative.
- Xvfb (only needed if we ever want --offscreen mode; interactive VTK window
  uses the VNC X server directly)

## Security group

- SSH (22) from the user's IP only
- HTTPS (443) from the internet (Caddy needs it; Let's Encrypt DNS-01 does
  not, but the scientist does)
- Nothing else. 8443 is internal only — Caddy reaches KasmVNC via localhost.

## Non-obvious gotchas (don't rediscover these)

1. **Claude Code OAuth: device-code flow grants API plan, not Max.**
   The copy-paste-the-code-on-your-laptop flow authenticates but ends up with
   `plan: api` (pay per token). Only the *local browser* OAuth flow — where
   `xdg-open` launches a browser on the VM itself and the callback hits
   `localhost:NNNN` — grants Claude Max subscription scopes. The playbook
   must install `xdg-utils` so Claude Code can launch a browser, and instruct
   the operator to run `claude /login` from a VNC terminal (not SSH) with
   `DISPLAY` set.

2. **xdg-utils is the real gating package.** Without it, `claude /login` can't
   launch a browser at all, and errors look like X11 / snap sandbox / display
   problems. Once `xdg-utils` is installed, the default snap Firefox (or snap
   Chromium) works fine under the VNC — the "snap browsers don't work in VNC"
   symptom was actually "no xdg-open binary on PATH." No need to avoid snap
   browsers.

3. **KasmVNC needs ssl-cert group membership.** The user needs to be in
   `ssl-cert` to read `/etc/ssl/private/`. Must log out/in after adding, or
   use `sg ssl-cert -c ...` in the playbook.

4. **First-run `vncserver` interactive prompts** can be pre-empted by writing
   `~/.vnc/kasmvnc.yaml` and `~/.vnc/passwd` (via `vncpasswd -f`) before the
   first invocation. Include a pre-created KasmVNC user with write access.

5. **IMDSv2** is the default on Ubuntu 24.04 — metadata calls need a session
   token, not just a GET.

6. **GPU quota reality.** "Running On-Demand G and VT instances" defaults to 0
   for new accounts, and *any* request > 0 goes to human review regardless of
   size. Requesting 4 vs 16 doesn't change the review path. Plan around
   multi-hour to multi-day approval latency; don't bet on it for a
   same-day demo. Quotas are per-region.

7. **Public IP changes on stop/start.** Auto-assigned public IPs are released
   when an instance stops. That's why the Route 53 update runs on every boot.
   (Elastic IPs would persist but cost $0.005/hr while idle — not worth it
   for our cadence.)

8. **"plan: api" in `/status` diagnosis tree**, in likelihood order:
   (a) `ANTHROPIC_API_KEY` set in env — `env | grep -i anthropic`, unset,
   remove from rc files. (b) Device-code OAuth path used — see gotcha #1;
   re-login via local browser flow. (c) Wrong option chosen in the login
   picker — `claude /logout`, `claude /login`, pick "Claude account"
   (subscription), not "Anthropic Console" / API key.

9. **XFCE cosmetic prompt on session start**: polkit asks to "create a color
   managed device" and expects the Linux user's password, which doesn't exist
   on EC2 (SSH-key-only user). Harmless — Cancel dismisses it. Playbook can
   silence it permanently with a `pkla` rule granting colord actions to all
   users.

## What stays manual

- `claude /login` via local browser OAuth (required for Max plan scopes; can't
  be automated because the token is device-bound).
- Sharing the VNC password with the scientist (out-of-band).

## Playbook structure (target)

```
demo-infra/
  provision.yml        # full provision: AWS + in-instance
  terminate.yml        # tear down instance
  group_vars/
    all.yml            # domain, email, instance type, KasmVNC version, etc.
  roles/
    aws_infra/         # SG, IAM role, instance, Route 53 record
    desktop/           # xfce, kasmvnc, xdg-utils, non-snap browser
    caddy/             # caddy install with route53 DNS plugin, Caddyfile
    vislang/           # node, claude-code, gh, vs code, repo clone, MCP reg
    boot_services/     # route53-update systemd oneshot, caddy enable,
                       #   kasmvnc autostart
```

Run: `ansible-playbook provision.yml` → wait ~8–12 min → ssh in, complete
`claude /login` via local browser → share URL + VNC password with scientist.

## Open questions / future work

- Packer-baked AMI for faster launch (~1 min vs ~10 min) if demo cadence
  increases.
- S3-stored Let's Encrypt cert reuse across instances (currently fresh cert
  per provision — fine at our frequency).
- If GPU quota lands, adapt to g4dn/g5 with VirtualGL for accelerated VTK
  rendering inside the VNC session.
