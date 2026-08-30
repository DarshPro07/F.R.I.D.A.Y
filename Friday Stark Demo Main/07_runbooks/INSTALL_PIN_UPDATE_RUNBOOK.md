# Install / Pin / Update Runbook

For each upstream:
1. clone to `third_party/upstream/<slug>`;
2. fetch tags/releases;
3. inspect current commit/version;
4. read LICENSE from pinned tree;
5. record exact SHA in UPSTREAM_LOCK;
6. preserve license/notices;
7. inspect install scripts before executing;
8. install in isolated env/container where practical;
9. run upstream tests;
10. create Friday adapter outside upstream;
11. create patch series only if upstream modification is unavoidable;
12. add deterministic health/start/stop;
13. run focused adapter/MCP tests;
14. run live Friday Golden Journey;
15. record rollback and status.

## Update
One upstream at a time:
`fetch → diff → license/security review → upstream tests → rebase patches → adapter tests → live journey → promote`.

Never bulk-update all 21.
