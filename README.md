# FDHub

**GitHub-native F-Droid binary repository generator.**

FDHub converts GitHub-hosted Android releases into a real, standards-compliant F-Droid repository, published automatically via GitHub Pages. No VPS, no database, no permanent infrastructure.

[![Validate](https://github.com/YOUR_USERNAME/YOUR_REPO/actions/workflows/validate.yml/badge.svg)](https://github.com/YOUR_USERNAME/YOUR_REPO/actions/workflows/validate.yml)

---

## How it works

```
You add an app JSON file
        ↓
Git push
        ↓
GitHub Actions validates your config
        ↓
Daily scheduled workflow:
  ├── Cheaply checks all configured repos for new releases
  ├── Downloads + inspects only new/changed APKs
  ├── Extracts metadata, verifies signatures
  ├── Generates F-Droid index-v2 (signed)
  └── Publishes to GitHub Pages
        ↓
F-Droid / Neo Store / Droid-ify clients
```

**First-time behavior**: When you add a new app, FDHub indexes only the **latest release**. Future releases are indexed incrementally as they appear. History accumulates forward.

---

## Quickstart

### 1. Fork this repository

Fork [FDHub](https://github.com/YOUR_USERNAME/fdhub) to your own GitHub account.

### 2. Enable GitHub Pages

Go to **Settings → Pages → Source → GitHub Actions**.

### 3. Generate a signing key

```bash
# Install FDHub locally
pip install -e .

# Generate signing key
fdhub init-signing
```

Follow the printed instructions to add secrets to your repository:
- `FDROID_KEYSTORE_BASE64` — base64-encoded keystore
- `FDROID_KEYSTORE_PASS` — keystore password
- `FDROID_KEY_ALIAS` — key alias (default: `repokey`)

> ⚠️ **Keep the keystore backed up.** If you lose it, you cannot update the repository without all clients needing to re-add it.

### 4. Configure your repository URL

Edit `config/config.json`:

```json
{
  "repoUrl": "https://YOUR_USERNAME.github.io/YOUR_REPO_NAME/repo",
  "webBaseUrl": "https://YOUR_USERNAME.github.io/YOUR_REPO_NAME"
}
```

### 5. Add an app

Create `apps/org.example.app.json`:

```json
{
  "id": "org.example.app",
  "name": "Example App",
  "github": "owner/repository"
}
```

### 6. Push and let it run

```bash
git add apps/org.example.app.json
git commit -m "Add Example App"
git push
```

The validate workflow runs immediately. The update workflow runs daily at midnight UTC (or trigger it manually from the Actions tab).

### 7. Add your repository to F-Droid

In F-Droid / Neo Store / Droid-ify, add:

```
https://YOUR_USERNAME.github.io/YOUR_REPO_NAME/repo
```

---

## App Configuration Reference

### Minimal (required fields only)

```json
{
  "id": "org.example.app",
  "name": "Example App",
  "github": "owner/repository"
}
```

### Full configuration

```json
{
  "id": "org.example.app",
  "name": "Example App",
  "github": "owner/repository",

  "releasePolicy": {
    "includePrereleases": false,
    "includeDrafts": false
  },

  "assets": {
    "include": ["*.apk"],
    "exclude": [
      "*-debug.apk",
      "*-unsigned.apk"
    ]
  },

  "signing": {
    "allowedCertificates": [
      "SHA256:AA:BB:CC:DD:EE:FF:..."
    ]
  },

  "metadata": {
    "summary": "Short description (max 80 chars)",
    "description": "Full description. Supports **markdown**.",
    "license": "GPL-3.0-or-later",
    "website": "https://example.org",
    "categories": ["Productivity", "Security"]
  },

  "retention": {
    "maxVersions": null
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | ✅ | Android package ID (must match APK) |
| `name` | ✅ | Display name |
| `github` | ✅ | `owner/repository` slug |
| `releasePolicy.includePrereleases` | ❌ | Default: `false` |
| `releasePolicy.includeDrafts` | ❌ | Default: `false` |
| `assets.include` | ❌ | Default: `["*.apk"]` |
| `assets.exclude` | ❌ | Default: debug/unsigned patterns |
| `signing.allowedCertificates` | ❌ | SHA-256 fingerprints; empty = any cert |
| `retention.maxVersions` | ❌ | Default: `null` (keep all) |

---

## CLI Reference

```bash
# Validate all app configurations
fdhub validate

# Check for new releases (no downloads)
fdhub check
fdhub check org.example.app

# Process a specific app
fdhub process org.example.app
fdhub process org.example.app --dry-run

# Process all apps
fdhub process-all
fdhub process-all --dry-run

# Generate F-Droid index from current state
fdhub generate-index

# Print the latest run report
fdhub report

# Generate signing key (run once)
fdhub init-signing

# Load full historical releases for an app
fdhub backfill org.example.app
```

---

## Architecture

```
apps/*.json              → App configuration (human-managed)
state/apps/*.json        → Persistent discovery state (machine-managed)
cache/apk/*.json         → APK metadata cache (disposable)
generated/repo/          → F-Droid repository output (published)
generated/reports/       → Run reports
src/fdhub/               → Python source
.github/workflows/       → CI/CD
```

### Scaling model

FDHub is designed for **check many, process few**:

```
5,000 apps
    ↓
cheap ETag-based daily checks
    ↓
4,970 unchanged → skip
30 changed
    ↓
download + inspect only those 30 apps' new APKs
    ↓
merge + generate index + sign + publish
```

New apps are indexed with only their **latest release** on first add. Subsequent new releases are indexed incrementally as they appear.

---

## Security

- APKs are **never executed** — all analysis is static
- APKs remain hosted on **upstream GitHub Releases** — FDHub does not re-host them
- Repository index is **cryptographically signed** (RSA, via `apksigner`)
- Signing key is **never committed to Git** — stored in GitHub Actions Secrets
- Package ID mismatches → **quarantine** (not silent publication)
- Certificate changes → **warning/quarantine**
- Path traversal prevention, URL safety validation

---

## GitHub Actions Secrets Required

| Secret | Description |
|--------|-------------|
| `FDROID_KEYSTORE_BASE64` | Base64-encoded JKS keystore |
| `FDROID_KEYSTORE_PASS` | Keystore password |
| `FDROID_KEY_ALIAS` | Key alias (default: `repokey`) |
| `FDHUB_GITHUB_TOKEN` | PAT for higher API limits (optional; falls back to `GITHUB_TOKEN`) |

---

## License

Apache 2.0. See [LICENSE](LICENSE).
