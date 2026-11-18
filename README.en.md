# MFA Gate

MFA Gate is an open-source, self-hosted two-factor authentication server for VPNs and internal applications. A VPN gateway asks MFA Gate over RADIUS. MFA Gate checks the password (local or LDAP/AD), applies an access policy and asks the employee to confirm the sign-in on their phone.

License: [Apache-2.0](LICENSE). The main documentation is in Russian ([README.md](README.md)).

## Features

- RADIUS (UDP 1812): PAP plus Access-Challenge for one-time codes. Message-Authenticator is required by default (BlastRADIUS mitigation). Gateway retransmissions are deduplicated.
- Push approval from the Android app. The decision is signed with an ECDSA P-256 key kept in Android Keystore and unlocked by biometrics or the device PIN.
- TOTP (RFC 6238), works offline.
- LDAP / Active Directory: multiple controllers, LDAPS/STARTTLS, nested groups, step-by-step diagnostics.
- Policies by resource and group, with `mfa` / `allow` / `deny` actions.
- REST API v1 with OpenAPI (`/api/docs`), scoped API keys.
- Web admin panel with roles and an audit log.
- Docker Compose deployment and the `mfagatectl` tool: install, update with automatic rollback, backups, diagnostics.

## Quick start

Requirements: Linux, Docker 24+, Docker Compose v2.

```bash
git clone https://github.com/calleograph/mfa-gate.git
cd mfagate/deploy
cp .env.example .env && chmod 600 .env
${EDITOR:-nano} .env      # MFA_GATE_PUBLIC_URL, MFA_GATE_IMAGE, ports
./mfagatectl install      # DB password, master key, image, migrations, start
./mfagatectl admin admin  # first superadmin; the password is printed once
```

Put the panel behind a TLS reverse proxy (see `docs/install.md`), sign in and enrol TOTP. Keep a copy of `deploy/data/secrets/master.key` apart from the backups.

## Status (0.1.0)

- Covered by end-to-end tests on a local stack: RADIUS PAP/Challenge, signed push, TOTP, policies, REST API.
- Experimental: HMS (Huawei Push Kit) and UnifiedPush delivery.
- Not implemented: RADIUS Accounting, reply attributes (Class, Filter-Id, VSA), RADIUS "second factor only" mode.
- Needs verification on real VPN gateways, a real Active Directory and real Android devices.

## Documentation

`docs/install.md`, `docs/admin-guide.md`, `docs/user-guide.md`, `docs/integrations.md`, `docs/api.md`, `docs/push.md`, `docs/ldap.md`, `docs/protocol/device-api.md`. Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).
