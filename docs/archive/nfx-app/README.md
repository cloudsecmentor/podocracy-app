# test-npx-app

A minimal **desktop web app** you can launch with a single `npx` command — the
same pattern as `npx @deepseek-ai/dsh web`. It's a zero-dependency Node CLI that
starts a local web server and opens the UI in your browser.

## Quick start

From this folder, without publishing:

```bash
# run the CLI directly
node bin/cli.js web

# or via npm script
npm start
```

Once published to npm, anyone could run it with:

```bash
npx test-npx-app web
```

By default it serves the app at **http://127.0.0.1:3080** and opens your browser.

## Commands

```
npx test-npx-app web        Start the server and open the browser
npx test-npx-app help       Show help
npx test-npx-app version    Print the version
```

### Options for `web`

| Option           | Default     | Description                          |
| ---------------- | ----------- | ------------------------------------ |
| `-p, --port <n>` | `3080`      | Port to listen on                    |
| `--host <h>`     | `127.0.0.1` | Host to bind to                      |
| `--no-open`      | —           | Don't open the browser automatically |

Examples:

```bash
npx test-npx-app web --port 4000
npx test-npx-app web --host 0.0.0.0 --no-open
```

## How it works

```
test-npx-app/
├── bin/cli.js      # CLI entry (bin), parses args, dispatches commands
├── src/server.js   # HTTP server: static files + /api/hello, opens browser
└── public/         # Frontend (index.html, styles.css, app.js)
```

- `bin/cli.js` is registered as the package `bin`, so `npx test-npx-app` resolves
  to it.
- The `web` command starts an `http` server (no dependencies), serves the
  `public/` folder, exposes a tiny `GET /api/hello` JSON endpoint, and launches
  the default browser.
- The frontend calls `/api/hello` to demonstrate the frontend ↔ backend loop.

## Try it locally as if published

```bash
npm link           # symlinks the bin globally
test-npx-app web   # now available as a command
# ...
npm unlink -g test-npx-app
```

## License

MIT
