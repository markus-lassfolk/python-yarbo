# Security Policy

## Supported Versions

python-yarbo is under active development. Security fixes are applied to the **latest release** only.

| Version | Supported          |
| ------- | ------------------ |
| 0.x     | ✅ Latest release  |
| < 0.1   | ❌ Not supported   |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in python-yarbo, please report it privately:

1. **GitHub Private Advisory** *(preferred)*: Go to
   [Security → Advisories](https://github.com/markus-lassfolk/python-yarbo/security/advisories/new)
   and click "Report a vulnerability".

2. **Email**: If you cannot use GitHub Advisories, contact the maintainer directly.
   Include "[python-yarbo Security]" in the subject line.

### What to include

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept (if safe to share)
- Affected versions
- Any suggested mitigations

### Response timeline

| Milestone                      | Target SLA           |
| ------------------------------ | -------------------- |
| Acknowledgement of report      | 48 hours             |
| Initial assessment             | 5 days               |
| Fix / patch (if confirmed)     | 30 days              |
| Public disclosure              | After fix is released |

## Security Considerations

python-yarbo communicates with Yarbo robot mowers over **local MQTT (plaintext)**.
Keep the following in mind:

- **Credentials**: Never hard-code passwords, API keys, or serial numbers in code.
  Use environment variables or a secrets manager. The RSA public key extracted from
  the APK is not sensitive and can be committed; private keys and passwords must not be.

- **Network exposure**: The Yarbo EMQX broker listens on port 1883 (plaintext) on
  the local network. Do **not** expose this port to the internet. Restrict access
  with your router's firewall.

- **MQTT authentication**: The local EMQX broker appears to accept anonymous connections.
  Treat the local network as a trust boundary — ensure only authorised devices have WiFi access.

- **Serial numbers**: Your Yarbo serial number (SN) is used as an MQTT topic component.
  It is not a secret, but avoid sharing it publicly to reduce exposure.

- **Cloud API**: The cloud REST API is migrating to AWS SigV4 auth. JWT tokens
  (30-day lifetime) should be treated as sensitive credentials.

## Scope

The following are **in scope** for security reports:

- Credential exposure or insecure credential handling in library code
- Unsafe use of `eval`, `exec`, or similar constructs
- Insecure defaults that could expose user credentials or device access

The following are **out of scope**:

- Vulnerabilities in the Yarbo firmware or cloud service (report to Yarbo directly)
- MQTT broker configuration issues (report to your broker vendor)
- Issues only reproducible on Python versions below 3.11 (unsupported)
- Issues in third-party dependencies (report upstream; we will update dependencies)
