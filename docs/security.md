# Security Notes

- Provider keys are read from environment variables only.
- The API writes project params, status, logs, and manifests, but not provider keys.
- The web container enables HTTP Basic Auth when `PORTAL_ADMIN_PASSWORD` is set.
- Do not expose the portal publicly without HTTPS and authentication.
- Do not mount the Docker socket into the web or API container.
- Before publishing the repo, run a secret scan and a personal-path scan.
