# Tau telemetry Worker

This Worker accepts `POST /api/report-install` with a JSON body containing only
Tau's version. D1 stores one aggregate counter per version. The Worker does not
persist request IP addresses, identifiers, prompts, paths, models, or session
data. Cloudflare necessarily processes request metadata as the HTTP provider.

## Deploy

```bash
cd .cloudflare/telemetry
npx wrangler@latest login
npx wrangler@latest d1 create tau-telemetry
```

Copy the returned database ID into `wrangler.jsonc`, then initialize and deploy:

```bash
npx wrangler@latest d1 execute tau-telemetry --remote --file=./schema.sql
npx wrangler@latest deploy
```

Inspect aggregate counts with:

```bash
npx wrangler@latest d1 execute tau-telemetry --remote \
  --command="SELECT version, count FROM version_counts ORDER BY version DESC"
```
