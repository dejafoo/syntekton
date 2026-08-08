# Generated host transport types

`openapi.ts` is produced by `openapi-typescript` from
`../openapi-v2.json`.

Regenerate:

```bash
bash scripts/generate_host_openapi.sh
npx --yes openapi-typescript contracts/host/openapi-v2.json -o contracts/host/generated/openapi.ts
cp contracts/host/generated/openapi.ts dashboard/src/generated/host-openapi.ts
bash scripts/check_openapi_drift.sh
```

Do not hand-edit generated files. Domain helpers and delivery logic stay handwritten.
