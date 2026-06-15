# Remote Ubuntu Setup

Recommended VM shape for alpha testing: Ubuntu 24.04 LTS, 2 vCPU, 8 GB RAM, 40 GB disk.

1. Install Docker and the Compose plugin.
2. Copy this repo to the VM.
3. Copy a provider-key env file to the VM, outside any public web path.
4. Start the portal:

```bash
export PODOCRACY_ENV_FILE=/opt/podocracy-worker-portal/.env
export PORTAL_ADMIN_PASSWORD='replace-with-a-strong-password'
docker compose up --build -d
```

5. Restrict the firewall to SSH and the portal port during testing.

For production-style remote access, put the portal behind HTTPS with Caddy, Nginx, or a cloud load balancer, and keep `PORTAL_ADMIN_PASSWORD` enabled.
