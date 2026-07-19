# Security Policy

## Supported versions

Only the latest release on the `main` branch receives security fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, report privately via
[GitHub Security Advisories](https://github.com/CJ7862/HiringCafe-Scraper/security/advisories/new)
("Report a vulnerability" button on the repository's Security tab).

Include:

- A description of the vulnerability and its impact
- Steps to reproduce (proof of concept if possible)
- Affected component (CLI, web UI, or API service)

You can expect an acknowledgement within 72 hours. Once a fix is released,
we'll credit you in the release notes unless you prefer to remain anonymous.

## Scope notes

- The API service is designed to run behind TLS (see service/README.md). Reports
  about deployments that skip TLS or expose Postgres/Redis publicly are
  configuration issues, not vulnerabilities.
- The web UI (webapp/) is a local development tool bound to 127.0.0.1 and is
  not hardened for public exposure.
