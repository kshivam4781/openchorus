# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.x | Yes |

## Reporting a vulnerability

Please do **not** open a public issue for security problems.
Report them privately through
[GitHub security advisories](https://github.com/kshivam4781/openchorus/security/advisories/new).
You will get a reply within a few days, and a fix or a plan within two weeks for
confirmed issues.

## Deployment notes

OpenChorus is built to run on your own machine.

- By default it listens on `127.0.0.1` only. If you set `OPENCHORUS_HOST=0.0.0.0` to share
  it on a network, put it behind a reverse proxy with authentication: the app itself has
  no user accounts yet.
- The AI service (`ai_service/`, port 8002) and ACE-Step (port 8001) must stay on
  localhost. They accept file paths and are not meant to be exposed.
- Uploads are limited by type and size, and files are stored under `OPENCHORUS_DATA`.
