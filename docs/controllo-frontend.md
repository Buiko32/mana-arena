# Verifica visiva locale

La frontend richiede il server FastAPI: aprire `app/static/index.html` con un server di soli file non fornisce le API `/api` e può produrre errori fuorvianti. Usare una copia di prova con dati separati.

In PowerShell, dalla cartella del progetto, avviare il server in un terminale dedicato:

```powershell
$env:DATA_DIR = Join-Path (Get-Location) '.taste-skill/diagnostic-data'
$env:SHARED_ACCESS_PASSWORD = 'Diagnostic-local-group-42'
$env:COOKIE_SECURE = 'false'
$env:ADMIN_PASSWORD = 'Diagnostic-local-password-42'
$env:SESSION_SECRET = 'diagnostic-local-secret-only-1234567890'
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8765
```

Sono credenziali esclusivamente per il database di prova locale, non per l'installazione reale. Fermare il server con Ctrl+C al termine. Se la porta è occupata, sceglierne un'altra anche nei comandi seguenti.

In un secondo terminale, usare la skill installata sul computer:

```powershell
$skill = Join-Path $env:USERPROFILE '.codex/skills/taste-skill'
node "$skill/scripts/shoot.mjs" --url http://127.0.0.1:8765/ --w 1440 --out .taste-skill/live-1440.png --profile .taste-skill/chrome-check
node "$skill/scripts/shoot.mjs" --url http://127.0.0.1:8765/ --w 390 --out .taste-skill/live-390.png --profile .taste-skill/chrome-check
node "$skill/scripts/gate.mjs" --start http://127.0.0.1:8765/ --kind software --category 'gestione tornei Magic' --primary 'Esplora i tornei'
node "$skill/scripts/gate.mjs" http://127.0.0.1:8765/
```

Eseguire i controlli uno alla volta. In Codex su questo PC Chrome è stato trovato ma si è chiuso subito nel sandbox; gli stessi comandi hanno funzionato fuori dal sandbox, con il server legato a `127.0.0.1`. Se accade nuovamente, richiedere l'esecuzione fuori dal sandbox. Il risultato `GATE: FAIL` indica segnalazioni sul layout, non un errore di avvio del browser. Il gate è progettato per pagine promozionali a schermo intero: valutare le segnalazioni nel contesto di un'applicazione operativa.
