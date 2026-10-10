# Floor Tycoon: game frontend

The 2.5D workplace game, ready to serve from a container and talk to the backend.

- `FRONTEND.md`: how the game calls the API, call by call. Start here.
- `CLAUDE.md`: what Claude Code should do with this folder.
- `public/`: the game as served. `src/`: the source to rebuild it.
- `mock-api/`: reference mock of the API contract. `e2e/`: a scripted full play that checks the calls.
- `docker-compose.yml`: web (nginx + game) + api + db, plus `mock` and `test` profiles.

Verified on 10 Oct 2026: the game played a full session against `mock-api` in a 844×390 browser, and `e2e/play.mjs` printed E2E PASSED (sessions, events, finish, floor, wall post, wall read). The Docker images were written but not built in that environment, which had no access to Docker Hub.
