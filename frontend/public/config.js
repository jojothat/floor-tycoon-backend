// Runtime config for the game. Replaced per environment (docker entrypoint writes it from env vars).
window.FT_CONFIG = {
  apiBase: '/api',          // where the backend is, relative to the game; '' turns the backend off (old playtest behaviour)
  pilotToken: 'dev-pilot',  // the pilot site's invite token
  gameVersion: 'dev',
  simMs: 20000              // length of the simulated day in ms (tests can shorten it)
};
