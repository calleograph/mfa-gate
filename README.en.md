<div align="center">

# MFA Gate

### A second factor for VPN and corporate services — on your own server

[![CI](https://github.com/calleograph/mfa-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/calleograph/mfa-gate/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/calleograph/mfa-gate)](https://github.com/calleograph/mfa-gate/releases)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

<img src="docs/images/overview-light.png" alt="MFA Gate admin panel: system state, sign-ins, sign-in problems" width="900">

[Русский](README.md) · [Documentation (Russian)](wiki/Home.md) · [Releases](https://github.com/calleograph/mfa-gate/releases)

</div>

---

## What is MFA Gate

MFA Gate protects your organization's remote access with a second factor. Employees type their usual domain password into the VPN client and then approve the sign-in on their phone — one tap with a fingerprint, or a code from an authenticator app. A leaked password is no longer enough: the attacker does not have the employee's phone.

It installs with one command on your server and works with your Active Directory. Passwords, keys, sign-in logs and employee data stay inside your organization: there is no cloud intermediary, and the project's developers never receive your data. The only thing that leaves is a short "new request" signal to the phone — no login, address or sign-in details.

## How it works for an employee

1. **One-time setup in a couple of minutes.** The employee opens the self-service portal, signs in with their domain account, picks the phone app or any authenticator app, scans a QR code and receives backup codes.
2. **Every VPN sign-in: as before, plus one tap.** The phone shows the request — resource, location, time — and the employee approves it with a fingerprint.
3. **Prefer codes?** Instead of the MFA Gate app, any authenticator app works: six offline digits, and the VPN client asks for the code after the password.
4. **Lost phone?** Sign in with a backup code and link a new one, or the administrator allows re-enrollment for 24 hours with one click.

## Features

| Feature | Details |
| --- | --- |
| **Employee portal** | domain sign-in, enrollment wizard, backup codes, own phones and apps, sign-in history, sessions |
| **Phone approval** | Android app; the decision is signed with a hardware-backed key after fingerprint or PIN, and the server accepts only signed answers from a linked phone |
| **TOTP codes** | any authenticator app (RFC 6238), offline, single use |
| **VPN over RADIUS** | Cisco ASA, FortiGate, pfSense, OpenVPN, Windows NPS any RADIUS PAP gateway, including gateways that check the password themselves; BlastRADIUS protection; gateway retries never create a second push |
| **Active Directory / LDAP** | multiple controllers, LDAPS and StartTLS, nested groups, step-by-step connection diagnostics |
| **Access policies** | where the second factor is required, who may sign in with a password only, who is denied; allowed methods |
| **Safe first enrollment** | only from the company network, only for allowed groups and only while the employee has no factor yet |
| **Admin panel** | users, phones, gateways, policies, sign-in history with the reason for every rejection, admin audit log, roles, light and dark themes, mobile-friendly |
| **Addresses and publishing** | a page for network admins: which addresses and ports to open, for whom, with ready nginx and Caddy configs |
| **REST API** | add the second factor to your own applications; OpenAPI, scoped keys with address restrictions |
| **Operations** | one-command install, updates with automatic rollback, backups and restore, diagnostics, emergency admin access |

## Installation

Ubuntu 22.04+ or Debian 12, 1 vCPU, 1 GB RAM, 5 GB disk. The installer sets up Docker itself.

```bash
git clone https://github.com/calleograph/mfa-gate.git
cd mfa-gate
sudo ./install.sh
```

The installer prints the admin panel address and a one-time password. Keep a copy of `deploy/data/secrets/master.key` away from the server: backups cannot be restored without it. Update from the project directory with `sudo git pull && sudo mfagatectl update 0.3.3` — a database backup is taken first and a failed update rolls back automatically.

## Android app

**MFA Gate Authenticator**, Android 8.0+, on the [Releases](https://github.com/calleograph/mfa-gate/releases) page. `gms` — phones with Google services; `foss` — phones without them, with its own connection to the server for instant requests.

## What is tested

Every change runs unit tests, tests against a real OpenLDAP and a full Docker end-to-end stack: installation, RADIUS sign-in with phone approval, codes, policies, the portal, update, rollback, backup and restore. Not yet verified on real hardware: specific VPN gateway models, a real Active Directory, the app on a range of phones, Firebase delivery. Details: [docs/testing.md](docs/testing.md).

## License

[Apache License 2.0](LICENSE) — free to use, modify and embed, including commercially. Security reports: [SECURITY.md](SECURITY.md). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).
