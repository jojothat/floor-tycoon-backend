// Written at container start from RO_* env (docker/40-write-config.sh). Edit by hand for local runs.
// apiBase: where /v1/read/* lives ('/api' behind the nginx proxy). Empty → the page shows the bundled sample data.
// readToken: dev/staging viewer token only. In production leave it empty and protect the readout with SSO at the proxy.
window.RO_CONFIG = { apiBase: '/api', readToken: 'dev-viewer' };
