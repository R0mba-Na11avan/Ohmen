# Security policy

Ohmen stores API keys and runs a local server, so security reports are
taken seriously.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through GitHub:
**Security › Report a vulnerability** on this repository
([direct link](https://github.com/R0mba-Na11avan/Ohmen/security/advisories/new)).

Include what you found, how to reproduce it, and the version you tested. You'll
get an acknowledgement within a week. This is a volunteer project, so fixes may
take a little longer, but you'll be kept informed and credited unless you'd
rather not be.

## Supported versions

Only the latest release gets security fixes.

## Scope notes

- The desktop app starts a local API server bound to `127.0.0.1`. Every request
  must carry a random per-launch token, and loopback `Host` headers are enforced
  to block DNS rebinding. Issues that bypass either are in scope.
- API keys are encrypted with the OS keychain (`safeStorage`). On Linux without a
  keyring, Electron falls back to weak obfuscation; the app warns about this in
  Settings. That fallback is known behavior, not a vulnerability.
- `pcb serve --no-auth` deliberately turns the token off for local testing.
