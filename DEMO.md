# Testing Guide — Qdrant RBAC mit OIDC

End-to-end Anleitung zum Testen des `qdrant-mcp-rbac` MCP-Servers mit der `demo/client` CLI.

```
┌──────────────┐  OIDC password   ┌───────────────────┐
│ demo/client  │ ───────────────▶ │ Keycloak (papaia) │
│ (CLI)        │ ◀──── token ──── │                   │
└──────────────┘                  └───────────────────┘
       │
       │  Bearer <access_token>
       ▼
┌──────────────┐    Qdrant JWT    ┌──────────┐
│ qdrant-mcp-rbac  │ ───────────────▶ │  Qdrant  │
│ (MCP Server) │                  │ (Docker) │
└──────────────┘                  └──────────┘
       ▲
       │  tool_calls
┌──────────────┐
│  LLM         │
└──────────────┘
```

---

## Voraussetzungen

- Docker Desktop läuft
- Python 3.11+ und `uv` installiert (`winget install astral-sh.uv`)
- VS Code + [Python-Extension](https://marketplace.visualstudio.com/items?itemName=ms-python.python) (für Debugging)
- Keycloak Admin-Zugang auf `https://auth.papaia.die-boehms.online`

---

## Schritt 1 — Keycloak einrichten (einmalig)

Öffne die Keycloak-Admin-Console für den Realm **papaia**.

### 1a. Client `mcp-qdrant` anlegen

| Einstellung | Wert |
|---|---|
| Client ID | `mcp-qdrant` |
| Client authentication | ON (confidential) |
| Direct access grants | ON |
| Standard flow | optional |

Unter **Credentials** → Client-Secret notieren (wird in `.env` gebraucht).

### 1b. Audience Mapper

Damit der MCP-Server den Token akzeptiert, muss der `aud`-Claim den Wert `mcp-qdrant` enthalten:

1. Client `mcp-qdrant` → **Client scopes** → `mcp-qdrant-dedicated` öffnen
2. **Add mapper** → **By configuration** → **Audience**
3. Name: `mcp-qdrant-audience`, Included Client Audience: `mcp-qdrant`, Add to access token: ON

### 1c. Realm-Rollen anlegen

| Rolle | Bedeutung |
|---|---|
| `qdrant-admin` | Break-glass Admin — globaler Qdrant-Zugriff |
| `finance` | Lesezugriff auf Collection `finance` |
| `it` | Lesezugriff auf Collection `it` |
| `sales` | Lesezugriff auf Collection `sales` |

**Realm roles** → **Create role** für jede Rolle.

### 1d. Test-User anlegen und Rollen zuweisen

| User | Passwort | Realm-Rolle |
|---|---|---|
| `admin` | frei wählbar | `qdrant-admin` |
| `alice` | frei wählbar | `finance` |
| `bob` | frei wählbar | `it` |
| `carol` | frei wählbar | `sales` |

Für jeden User: **Users** → User öffnen → **Role mapping** → **Assign role**.

---

## Schritt 2 — Qdrant starten (Docker)

### 2a. `src/.env` konfigurieren

```bash
cp src/.env.example src/.env
```

Werte in `src/.env` anpassen:

```env
OIDC_ISSUER_URL=https://auth.papaia.die-boehms.online/realms/papaia
OIDC_AUDIENCE=mcp-qdrant
OIDC_JWKS_CACHE_TTL=3600

QDRANT_URL=http://localhost:6333
QDRANT_JWT_SECRET=ein-langes-zufaelliges-geheimnis   # z.B. openssl rand -hex 32
QDRANT_JWT_TTL=3600

MCP_HOST=0.0.0.0
MCP_PORT=8000
MCP_PATH=/mcp

LOG_LEVEL=INFO

RBAC_ADMIN_ROLE=qdrant-admin
RBAC_ACL_COLLECTION=_rbac_acl
RBAC_ACL_CACHE_TTL=60
RBAC_SERVICE_TOKEN_TTL=60
```

> **Wichtig:** `QDRANT_JWT_SECRET` wird als Qdrant-API-Key verwendet und muss identisch mit dem Wert in `docker-compose.yml` sein — es gibt nur eine Quelle, da docker-compose `src/.env` direkt einliest.

### 2b. Docker Compose starten

```bash
docker compose --env-file src/.env -f docker/docker-compose.yml up -d qdrant
```

Verify: `http://localhost:6333/dashboard` zeigt das Qdrant-Dashboard.

---

## Schritt 3 — Demo-Daten bootstrappen

Erstellt die Collections `finance`, `it` und `sales` mit eingebetteten Markdown-Chunks aus `demo/data/`.

```bash
cd demo/bootstrap
cp .env.example .env
```

`demo/bootstrap/.env` anpassen:

```env
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=ein-langes-zufaelliges-geheimnis   # gleicher Wert wie QDRANT_JWT_SECRET

# OpenAI-kompatibler Embedding-Endpunkt — z.B. Ollama lokal oder ein Cloud-Dienst
EMBEDDING_API_URL=http://localhost:11434/v1
EMBEDDING_API_KEY=                                 # leer für unauthentifizierte lokale Endpunkte
EMBEDDING_MODEL=all-MiniLM-L6-v2

DATA_DIR=../data
```

> **Hinweis:** Bootstrap ruft keinen lokalen SentenceTransformers-Code mehr auf,
> sondern sendet Texte an den konfigurierten OpenAI-kompatiblen HTTP-Endpunkt.
> Für Ollama muss das Modell vorher mit `ollama pull all-MiniLM-L6-v2` (oder
> einem gleichwertigen Embedding-Modell) heruntergeladen worden sein.

```bash
uv sync
uv run python vectorize.py
```

Erwartete Ausgabe: je eine Collection pro `.md`-Datei in `demo/data/`.

---

## Schritt 4 — MCP Server starten

```bash
cd src
uv sync
uv run python main.py
```

Server läuft auf `http://localhost:8000/mcp`.

```bash
# Health-Check
curl http://localhost:8000/health
# → {"status": "ok"}
```

---

## Schritt 5 — ACL-Grants konfigurieren (als Admin)

### 5a. Client-Demo für Admin konfigurieren

```bash
cd demo/client
cp .env.example .env
```

`demo/client/.env`:

```env
OIDC_ISSUER_URL=https://auth.papaia.die-boehms.online/realms/papaia
OIDC_CLIENT_ID=mcp-qdrant
OIDC_CLIENT_SECRET=<secret aus Schritt 1a>
OIDC_GRANT_TYPE=password
OIDC_USERNAME=admin
OIDC_PASSWORD=<admin-passwort>

MCP_SERVER_URL=http://localhost:8000/mcp

LLM_MODEL=anthropic/claude-3-5-sonnet-latest
# oder ein anderes litellm-Modell — API-Key via Env-Var setzen
# ANTHROPIC_API_KEY=sk-ant-...

LOG_LEVEL=INFO
```

### 5b. Admin-Session starten

```bash
uv sync
uv run python client.py
```

In der Chat-Shell die ACL-Grants anlegen:

```
> Zeige alle aktuellen ACL-Grants

> Gib der Rolle "finance" Lesezugriff auf die Collection "finance"
> Gib der Rolle "it" Lesezugriff auf die Collection "it"
> Gib der Rolle "sales" Lesezugriff auf die Collection "sales"

> Zeige alle ACL-Grants nochmal zur Bestätigung
```

Der LLM ruft dabei `grant_access` und `list_acl` auf. Nach jedem Grant wird der In-Memory-Cache automatisch invalidiert.

---

## Schritt 6 — RBAC testen (als normaler User)

### 6a. `finance`-User (alice)

`demo/client/.env` ändern:

```env
OIDC_USERNAME=alice
OIDC_PASSWORD=<alice-passwort>
```

Session starten und testen:

```
> Welche Collections stehen mir zur Verfügung?
```
→ Nur `finance` sichtbar (nicht `it`, `sales`, `_rbac_acl`).

```
> Suche in der finance Collection nach "budget"
```
→ Suchergebnisse aus `demo/data/finance.md`.

```
> Versuche, etwas in die IT Collection zu schreiben
```
→ Fehler: kein Zugriff.

```
> Füge einen neuen Punkt in die finance Collection ein
```
→ Fehler: nur Lesezugriff (`r`), kein Schreibzugriff (`rw`).

### 6b. `it`-User (bob)

```env
OIDC_USERNAME=bob
OIDC_PASSWORD=<bob-passwort>
```

```
> Suche nach "server" in der IT Collection
> Versuche, die finance Collection zu lesen
```
→ Nur `it`-Zugriff, `finance` verweigert.

### 6c. `sales`-User (carol)

```env
OIDC_USERNAME=carol
OIDC_PASSWORD=<carol-passwort>
```

Analog für die `sales` Collection.

---

## Schritt 7 — MCP Server debuggen (VS Code)

Die Datei `.vscode/launch.json` ist bereits im Repository enthalten.

### 7a. Server im Debug-Modus starten

1. In VS Code das Repo-Verzeichnis öffnen
2. `src/main.py` öffnen (optional)
3. **F5** drücken → Konfiguration **"qdrant-mcp-rbac MCP Server"** auswählen

Der Server startet mit `LOG_LEVEL=DEBUG` und debugpy attached.

### 7b. Sinnvolle Breakpoint-Positionen

| Datei | Klasse / Funktion | Was passiert dort |
|---|---|---|
| `src/mcp_app/middleware.py` | `OIDCAuthMiddleware.__call__` | Jeder eingehende MCP-Request |
| `src/auth/oidc.py` | `OIDCValidator.validate` | OIDC-Token-Validierung |
| `src/auth/acl.py` | `AclResolver.get_mapping` | ACL-Cache-Lookup / -Reload |
| `src/auth/jwt_builder.py` | `QdrantJWTBuilder.build` | Qdrant-JWT-Erstellung |
| `src/mcp_app/tools.py` | `_require_access` | Zugriffscheck pro Tool-Call |

### 7c. Debug-Tipps

- **`LOG_LEVEL=DEBUG`** (im launch.json bereits gesetzt) zeigt alle HTTP-Requests, JWKS-Fetches und Token-Details.
- Breakpoints in `middleware.py` halten bei _jedem_ Request an — auch bei Health-Checks. Im Debug-Panel den Call-Stack inspizieren.
- Uvicorn läuft im Debug-Modus als **Single-Process** (kein `--reload`). Nach Code-Änderungen einfach neu starten (Shift+F5, dann F5).
- Für verbose MCP-Frame-Logs: in `demo/client/.env` ebenfalls `LOG_LEVEL=DEBUG` setzen und den Client separat im Terminal starten.

---

## Troubleshooting

| Symptom | Wahrscheinliche Ursache | Lösung |
|---|---|---|
| `403 no_mapped_roles` | User hat keine ACL-Grants | Admin-Session öffnen, `grant_access` aufrufen (Schritt 5) |
| `401 invalid_token` (audience) | `aud`-Claim fehlt | Audience-Mapper in Keycloak prüfen (Schritt 1b) |
| `401 invalid_token` (signature) | JWKS-Issuer falsch | `OIDC_ISSUER_URL` in `src/.env` prüfen |
| Qdrant: `Unauthorized` | JWT-Secret stimmt nicht | `QDRANT_JWT_SECRET` in `src/.env` und docker-compose-Env vergleichen |
| `Connection refused :6333` | Qdrant-Container läuft nicht | `docker compose ... up -d` wiederholen; `docker ps` prüfen |
| JWKS-Fetch-Fehler | Keycloak nicht erreichbar | `curl https://auth.papaia.die-boehms.online/realms/papaia/.well-known/openid-configuration` |
| Client: `401` sofort | Client-Secret falsch | Secret aus Keycloak-Console neu kopieren (Schritt 1a) |
| LLM ruft kein Tool auf | Falsche LLM-Config | `LLM_MODEL`, `LLM_API_KEY` in `demo/client/.env` prüfen |
