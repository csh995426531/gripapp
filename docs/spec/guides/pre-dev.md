# Grip Pre-Dev Checklist

- [ ] Read `product.md` — is this change in MVP scope?
- [ ] Touching MCP tools? Update `mcp/contracts.md` + contract tests first.
- [ ] Touching secrets/SSH/cloud creds? Re-read `security/secrets.md`; add redaction test.
- [ ] New persisted fields? Update `tasks/store.md` or `resources/model.md`.
- [ ] Menubar/status.json? Ensure `assert_no_secrets` on write path.
- [ ] Do not put implementation inside `eva_link/` package — use `grip/`.
- [ ] Do not ask the user for step-by-step permission; follow locked MVP.
